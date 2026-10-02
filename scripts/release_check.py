"""Conservative publication checks; heuristic scan, not a secrecy guarantee."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'.git', '__pycache__', '.venv', 'demo-output'}
ALLOWED = {'.py','.mjs','.html','.md','.json','.yaml','.yml','.csv'}
SPECIAL = {'LICENSE','.gitignore'}
PATTERNS = [re.compile(r'\b[0-9a-f]{31,64}-[0-9a-f]{32,64}\b',re.I),
            re.compile(r'\bsk-[A-Za-z0-9_-]{24,}\b'),
            re.compile(r'[A-Za-z]:[\\/]Users[\\/][A-Za-z0-9_.-]+'),
            re.compile(r'[A-Za-z0-9_.+-]+@(?:gmail|hotmail|outlook)\.com',re.I)]

def public_files():
    return [p for p in sorted(ROOT.rglob('*')) if p.is_file() and not any(x in EXCLUDED for x in p.relative_to(ROOT).parts)]

def check():
    issues=[]
    files=public_files()
    for p in files:
        rel=p.relative_to(ROOT).as_posix()
        if p.name not in SPECIAL and p.suffix not in ALLOWED:
            issues.append(f'Unexpected file type: {rel}'); continue
        if p.suffix=='.csv' and not rel.startswith('examples/synthetic/'):
            issues.append(f'CSV outside synthetic example: {rel}')
        text=p.read_text(encoding='utf-8')
        if any(pattern.search(text) for pattern in PATTERNS):
            issues.append(f'Potential private content: {rel}')
        if p.suffix=='.md':
            for target in re.findall(r'\]\(([^ )]+)',text):
                if ':' in target or target.startswith('#'):
                    continue
                if not (p.parent/target.split('#')[0]).exists():
                    issues.append(f'Broken local link: {rel}: {target}')
    if issues:
        raise SystemExit('\n'.join(issues))
    print(f'Publication checks passed: {len(files)} text files; no matching private-content patterns. Manual review still required.')

if __name__=='__main__':
    check()
