"""按文件发布：给「直接在服务器上改文件」的网站用（不是 git 仓库、也可能有别人同时在改）。
批注服务把它当作 FB_DEPLOY_CMD 运行，例如：
  FB_DEPLOY_CMD=python3 /opt/agent-page-feedback/tools/deploy_files.py
  FB_SITE_ROOT=/var/www/example.com/site        网站根目录（发布包里的路径都相对它）
  FB_DEPLOY_BACKUPS=/opt/agent-page-feedback/deploy-backups   每次发布改动前的文件备份（默认在数据库旁边）

发布包（agent 用 tools/fb.py deploy --dir 生成）是 tar.gz：
  files/<相对路径>        新内容
  manifest.json          {"files": {"相对路径": {"base": "改之前的 sha256，新文件为 null"}}, "delete": {"相对路径": "sha256"}}

发布（FB_DEPLOY_ACTION=deploy）：
  1. 检查每个文件现在的内容还是 agent 改之前的那一版（base）。有人在这期间改过 → 冲突，什么都不改，退出码 3；
  2. 备份要动的文件（不存在的记为新增）；
  3. 逐个写入（先写临时文件再改名）。
回滚（FB_DEPLOY_ACTION=rollback，FB_ROLLBACK_ID=那次发布的编号）：
  只恢复那次发布动过的文件：改过的换回原样，新增的删掉，删掉的放回去。
  如果之后又有人改了这些文件 → 冲突，不动，退出码 3。回滚本身也会备份，可以再撤销。
"""
import hashlib, io, json, os, shutil, sys, tarfile, time

ROOT = os.path.realpath(os.environ.get('FB_SITE_ROOT', ''))
BK = os.environ.get('FB_DEPLOY_BACKUPS') or os.path.join(os.path.dirname(os.environ.get('FB_DB', './data/fb.db')), 'deploy-backups')
ACTION, DID = os.environ.get('FB_DEPLOY_ACTION', ''), os.environ.get('FB_DEPLOY_ID', '')


def sha(path):
    if not os.path.isfile(path): return None
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
    return h.hexdigest()


def live(rel):
    """相对路径 → 网站里的绝对路径；不允许跳出网站根目录。"""
    if not rel or rel.startswith('/') or '\\' in rel or any(p in ('', '.', '..') for p in rel.split('/')): raise SystemExit(f'路径不对：{rel}')
    p = os.path.realpath(os.path.join(ROOT, rel))
    if not p.startswith(ROOT + os.sep): raise SystemExit(f'路径跳出了网站目录：{rel}')
    return p


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.fb-tmp-{os.getpid()}'
    with open(tmp, 'wb') as f: f.write(data)
    if os.path.exists(path): shutil.copymode(path, tmp)
    else: os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def writable(rel):
    p = live(rel)
    if os.path.exists(p) and not os.access(p, os.W_OK): return False
    d = os.path.dirname(p)
    while not os.path.isdir(d): d = os.path.dirname(d)
    return os.access(d, os.W_OK | os.X_OK)


def apply(changes, note):
    """changes: {相对路径: 新内容 bytes 或 None（删除）}。先检查都能写，再备份，再写，返回备份记录。"""
    ro = [rel for rel in changes if not writable(rel)]
    if ro:
        print('这些文件（或所在目录）服务没有写权限，什么都没有改：'); [print('  ' + r) for r in ro]
        print('请负责人在服务器上把它们改成服务的运行用户所有（chown），或者人工处理。'); sys.exit(4)
    d = os.path.join(BK, DID); os.makedirs(os.path.join(d, 'before'), exist_ok=True)
    rec = {'id': DID, 'action': ACTION, 'note': note, 'time': time.time(), 'files': {}}
    for rel, data in changes.items():
        p = live(rel); old = sha(p)
        if old: shutil.copy2(p, os.path.join(d, 'before', rel.replace('/', '%2F')))
        rec['files'][rel] = {'before': old, 'after': hashlib.sha256(data).hexdigest() if data is not None else None}
    with open(os.path.join(d, 'record.json'), 'w', encoding='utf-8') as f: json.dump(rec, f, ensure_ascii=False, indent=1)
    for rel, data in changes.items():
        p = live(rel)
        if data is None:
            if os.path.exists(p): os.remove(p)
        else: write(p, data)
    return rec


def report(rec):
    for rel, x in rec['files'].items():
        kind = '新增' if not x['before'] else '删除' if not x['after'] else '修改'
        print(f'  {kind} {rel}')
    print(f'共 {len(rec["files"])} 个文件；备份在 {os.path.join(BK, DID)}')


def deploy():
    bundle = os.environ.get('FB_DEPLOY_BUNDLE', '')
    if not bundle or not os.path.isfile(bundle): raise SystemExit('这次发布没有发布包（agent 要用 tools/fb.py deploy --dir 发布）')
    with tarfile.open(bundle, 'r:gz') as t:
        man = json.load(t.extractfile('manifest.json'))
        changes = {rel: t.extractfile('files/' + rel).read() for rel in man.get('files', {})}
    for rel in man.get('delete', {}): changes[rel] = None
    if not changes: raise SystemExit('发布包是空的')
    bad = []
    for rel, m in man.get('files', {}).items():
        if sha(live(rel)) != m.get('base'): bad.append(rel)
    for rel, base in man.get('delete', {}).items():
        if sha(live(rel)) != base: bad.append(rel)
    if bad:
        print('冲突：这些文件在 agent 改之后又被别人改过（或 agent 没有基于最新版本修改），什么都没有改：')
        for rel in bad: print('  ' + rel)
        print('请重新取最新版本，在它的基础上修改后再发布。'); sys.exit(3)
    report(apply(changes, 'deploy'))


def rollback():
    rb = os.environ.get('FB_ROLLBACK_ID', '')
    d = os.path.join(BK, rb)
    try:
        with open(os.path.join(d, 'record.json'), encoding='utf-8') as f: rec = json.load(f)
    except OSError: raise SystemExit(f'找不到第 {rb} 次发布的备份')
    bad = [rel for rel, x in rec['files'].items() if sha(live(rel)) != x['after']]
    if bad:
        print(f'冲突：第 {rb} 次发布之后，这些文件又被改过，直接恢复会冲掉后来的改动，什么都没有改：')
        for rel in bad: print('  ' + rel)
        print('请人工确认，或者针对这几处单独再改一次。'); sys.exit(3)
    changes = {}
    for rel, x in rec['files'].items():
        if x['before']:
            with open(os.path.join(d, 'before', rel.replace('/', '%2F')), 'rb') as f: changes[rel] = f.read()
        else: changes[rel] = None
    report(apply(changes, f'rollback of {rb}'))
    print(f'已恢复到第 {rb} 次发布之前')


if __name__ == '__main__':
    if not os.environ.get('FB_SITE_ROOT') or not os.path.isdir(ROOT): raise SystemExit('请设置 FB_SITE_ROOT（网站根目录）')
    if not DID.isdigit(): raise SystemExit('缺少 FB_DEPLOY_ID')
    {'deploy': deploy, 'rollback': rollback}.get(ACTION, lambda: sys.exit(f'不认识的动作：{ACTION}'))()
