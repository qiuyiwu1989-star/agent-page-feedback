"""按文件发布：给「直接在服务器上改文件」的网站用（不是 git 仓库、可能有别人同时在改）。
批注服务把它当作 FB_DEPLOY_CMD 运行：
  FB_DEPLOY_CMD=python3 -I /opt/agent-page-feedback/tools/deploy_files.py
  FB_SITE_ROOT=/var/www/example.com/site     网站目录（nginx 提供的那个）
  FB_SITE_SOURCE=/srv/site-src               选填：源文件目录。设置后，先改源文件，再把同一批文件复制到网站目录，
                                             避免别人下次「从源文件发布」时把改动覆盖回去
  FB_SITE_MAP=/srv/site-src/deploy-map.json  选填：源文件 → 网站路径的对应关系（默认是源文件目录里的 deploy-map.json；
                                             没有这个文件时，源文件和网站路径相同）
  FB_DEPLOY_BACKUPS=...                      每次发布改动前的备份（默认在数据库旁边的 deploy-backups/）

deploy-map.json 的写法（键是源文件里的路径，值是网站路径；null 表示不发布）：
  {"ui21.css": "ui21/ui21.css",   精确对应
   "plan/": "plan/",              目录前缀
   "*.html": "same",              通配，网站路径和源文件路径相同
   "build*.py": null}             不发布
  匹配顺序：精确 → 最长的目录前缀 → 通配（按出现顺序）。

agent 一律用网站路径（例如 ui21/ui21.css）。发布包（tools/fb.py deploy --dir 生成）是 tar.gz：
  files/<网站路径>   新内容
  manifest.json     {"files": {"网站路径": {"base": "改之前的 sha256，新文件为 null"}}, "delete": {"网站路径": "sha256"}}

动作（FB_DEPLOY_ACTION）：
  read      取一个文件的当前版本（有源文件时取源文件）：FB_READ_PATH=网站路径；输出 JSON {path, source, sha, b64}
  deploy    1. 检查：每个文件现在还是 agent 取的那一版（base）；有源文件时，网站上的文件也要和改之前的源文件一致
               （不一致说明源文件有还没发布的改动，或网站被直接改过）。不满足 → 冲突，什么都不改，退出码 3；
            2. 检查服务对这些文件都有写权限，否则退出码 4，什么都不改；
            3. 备份要动的文件（源文件和网站各一份），再写入（先写临时文件再改名）。
  rollback  FB_ROLLBACK_ID=那次发布的编号。只恢复那次发布动过的文件；之后又有人改过 → 冲突，不动。回滚本身也会备份。
"""
import base64, fnmatch, hashlib, json, os, shutil, sys, tarfile, time

ROOT = os.path.realpath(os.environ.get('FB_SITE_ROOT', '') or '/nonexistent')
SRC = os.path.realpath(os.environ['FB_SITE_SOURCE']) if os.environ.get('FB_SITE_SOURCE') else ''
MAPF = os.environ.get('FB_SITE_MAP') or (os.path.join(SRC, 'deploy-map.json') if SRC else '')
BK = os.environ.get('FB_DEPLOY_BACKUPS') or os.path.join(os.path.dirname(os.environ.get('FB_DB', './data/fb.db')), 'deploy-backups')
ACTION, DID = os.environ.get('FB_DEPLOY_ACTION', ''), os.environ.get('FB_DEPLOY_ID', '')


def sha(path):
    if not path or not os.path.isfile(path): return None
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
    return h.hexdigest()


def under(base, rel):
    """相对路径 → base 下的绝对路径；不允许跳出 base。"""
    if not rel or rel.startswith('/') or '\\' in rel or any(p in ('', '.', '..') for p in rel.split('/')): raise SystemExit(f'路径不对：{rel}')
    p = os.path.realpath(os.path.join(base, rel))
    if not p.startswith(base + os.sep): raise SystemExit(f'路径跳出了目录：{rel}')
    return p


def load_map():
    if not SRC or not MAPF or not os.path.isfile(MAPF): return None
    with open(MAPF, encoding='utf-8') as f: return json.load(f)


def site_of(srel, m):
    """源文件路径 → 网站路径（None = 不发布）。"""
    if m is None: return srel
    if srel in m: return m[srel]
    pre = sorted((k for k in m if k.endswith('/') and srel.startswith(k)), key=len, reverse=True)
    if pre:
        v = m[pre[0]]; return None if v is None else v + srel[len(pre[0]):]
    for k, v in m.items():
        if not k.endswith('/') and any(c in k for c in '*?[') and fnmatch.fnmatchcase(srel, k):
            return None if v is None else (srel if v == 'same' else v)
    return None


def source_of(site_rel, m):
    """网站路径 → 源文件路径。没有源文件目录时返回 ''（直接改网站）；有源文件目录但对应不上时返回 None。"""
    if not SRC: return ''
    if m is None: return site_rel
    for k, v in m.items():                               # 精确对应
        if v == site_rel and not k.endswith('/') and not any(c in k for c in '*?['): return k
    for k, v in sorted(m.items(), key=lambda kv: -len(kv[0])):   # 目录前缀
        if k.endswith('/') and v and site_rel.startswith(v):
            cand = k + site_rel[len(v):]
            if site_of(cand, m) == site_rel: return cand
    return site_rel if site_of(site_rel, m) == site_rel else None


def targets(site_rel, m):
    """一个网站路径要写的位置：[(标签, 绝对路径)]，有源文件时先源文件后网站。"""
    s = source_of(site_rel, m)
    if s is None:
        raise SystemExit(f'{site_rel} 不是从源文件发布的（可能是生成器产出的页面）。请改覆盖层（例如 ui21/ui21.js、ui21/ui21.css），或者登记给维护生成器的人。')
    live = under(ROOT, site_rel)
    if s and not os.path.exists(under(SRC, s)) and os.path.exists(live):
        raise SystemExit(f'{site_rel} 在网站上有、在源文件里没有（可能是生成器产出的页面）。请改覆盖层（例如 ui21/ui21.js、ui21/ui21.css），或者登记给维护生成器的人。')
    return ([('源文件', under(SRC, s))] if s else []) + [('网站', live)]


def writable(p):
    if os.path.exists(p) and not os.access(p, os.W_OK): return False
    d = os.path.dirname(p)
    while not os.path.isdir(d): d = os.path.dirname(d)
    return os.access(d, os.W_OK | os.X_OK)


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.fb-tmp-{os.getpid()}'
    with open(tmp, 'wb') as f: f.write(data)
    if os.path.exists(path): shutil.copymode(path, tmp)
    else: os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def apply(changes, note):
    """changes: {绝对路径: 新内容 bytes 或 None（删除）}。先检查写权限，再备份，再写。"""
    ro = [p for p in changes if not writable(p)]
    if ro:
        print('这些文件（或所在目录）服务没有写权限，什么都没有改：'); [print('  ' + p) for p in ro]
        print('请把它们改成服务的运行用户所有（chown），或者人工处理。'); sys.exit(4)
    d = os.path.join(BK, DID); os.makedirs(os.path.join(d, 'before'), exist_ok=True)
    rec = {'id': DID, 'action': ACTION, 'note': note, 'time': time.time(), 'files': {}}
    for i, (p, data) in enumerate(changes.items()):
        old = sha(p); keep = f'{i}-{os.path.basename(p)}'
        if old: shutil.copy2(p, os.path.join(d, 'before', keep))
        rec['files'][p] = {'before': old, 'after': hashlib.sha256(data).hexdigest() if data is not None else None, 'keep': keep if old else None}
    with open(os.path.join(d, 'record.json'), 'w', encoding='utf-8') as f: json.dump(rec, f, ensure_ascii=False, indent=1)
    for p, data in changes.items():
        if data is None:
            if os.path.exists(p): os.remove(p)
        else: write(p, data)
    for p, x in rec['files'].items():
        kind = '新增' if not x['before'] else '删除' if not x['after'] else '修改'
        print(f'  {kind} {p}')
    print(f'共 {len(rec["files"])} 个文件；备份在 {d}')


def conflict(bad, why):
    print(f'冲突：{why}，什么都没有改：'); [print('  ' + b) for b in bad]
    print('请重新取最新版本，在它的基础上修改后再发布；拿不准就交给负责人。'); sys.exit(3)


def read():
    m = load_map(); rel = os.environ.get('FB_READ_PATH', '')
    tg = targets(rel, m); p = tg[0][1]
    b = open(p, 'rb').read() if os.path.isfile(p) else None
    print(json.dumps({'path': rel, 'source': tg[0][0] == '源文件', 'sha': hashlib.sha256(b).hexdigest() if b is not None else None,
                      'b64': base64.b64encode(b).decode() if b is not None else None}))


def deploy():
    bundle = os.environ.get('FB_DEPLOY_BUNDLE', '')
    if not bundle or not os.path.isfile(bundle): raise SystemExit('这次发布没有发布包（agent 要用 tools/fb.py deploy --dir 发布）')
    m = load_map()
    with tarfile.open(bundle, 'r:gz') as t:
        man = json.load(t.extractfile('manifest.json'))
        new = {rel: t.extractfile('files/' + rel).read() for rel in man.get('files', {})}
    for rel in man.get('delete', {}): new[rel] = None
    if not new: raise SystemExit('发布包是空的')
    bases = {rel: x.get('base') for rel, x in man.get('files', {}).items()}; bases.update(man.get('delete', {}))
    stale, drift, changes = [], [], {}
    for rel, data in new.items():
        tg = targets(rel, m)
        if sha(tg[0][1]) != bases[rel]: stale.append(rel)
        if len(tg) == 2 and sha(tg[1][1]) != sha(tg[0][1]): drift.append(rel)
        for _, p in tg: changes[p] = data
    if stale: conflict(stale, 'agent 取了这些文件以后，它们又被别人改过')
    if drift: conflict(drift, '网站上的这些文件和源文件不一致（源文件有还没发布的改动，或网站被直接改过）')
    apply(changes, 'deploy')


def rollback():
    rb = os.environ.get('FB_ROLLBACK_ID', ''); d = os.path.join(BK, rb)
    try:
        with open(os.path.join(d, 'record.json'), encoding='utf-8') as f: rec = json.load(f)
    except OSError: raise SystemExit(f'找不到第 {rb} 次发布的备份')
    for p in rec['files']:
        if not (p.startswith(ROOT + os.sep) or (SRC and p.startswith(SRC + os.sep))): raise SystemExit(f'备份记录里的路径不在网站或源文件目录里：{p}')
    bad = [p for p, x in rec['files'].items() if sha(p) != x['after']]
    if bad: conflict(bad, f'第 {rb} 次发布之后，这些文件又被改过，直接恢复会冲掉后来的改动')
    changes = {}
    for p, x in rec['files'].items():
        if x['before']:
            with open(os.path.join(d, 'before', x.get('keep') or ''), 'rb') as f: changes[p] = f.read()
        else: changes[p] = None
    apply(changes, f'rollback of {rb}')
    print(f'已恢复到第 {rb} 次发布之前')


if __name__ == '__main__':
    if not os.path.isdir(ROOT): raise SystemExit('请设置 FB_SITE_ROOT（网站目录）')
    if SRC and not os.path.isdir(SRC): raise SystemExit(f'FB_SITE_SOURCE 不是目录：{SRC}')
    if ACTION != 'read' and not DID.isdigit(): raise SystemExit('缺少 FB_DEPLOY_ID')
    {'deploy': deploy, 'rollback': rollback, 'read': read}.get(ACTION, lambda: sys.exit(f'不认识的动作：{ACTION}'))()
