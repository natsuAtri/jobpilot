# -*- coding: utf-8 -*-
"""Dependency-free PDF text extractor with coordinates.

Written to validate resume parsing before the real pipeline exists.
No xref reliance: objects are found by scanning, including those packed inside
/ObjStm streams. Supports FlateDecode, Type0/Identity-H CID fonts and
ToUnicode CMaps (bfchar + bfrange), which is what Chinese resumes need.
"""
import re
import sys
import zlib
import json
import unicodedata

OBJ_RE = re.compile(rb'(?<![0-9])(\d+)\s+(\d+)\s+obj\b')


# --------------------------------------------------------------- byte scanning
def _skip_string(d, i):
    depth = 0
    n = len(d)
    while i < n:
        c = d[i]
        if c == 0x5C:
            i += 2
            continue
        if c == 0x28:
            depth += 1
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _skip_hex(d, i):
    j = d.find(b'>', i + 1)
    return len(d) if j == -1 else j + 1


def _skip_dict(d, i):
    start, depth, n = i, 0, len(d)
    while i < n:
        c = d[i]
        if c == 0x28:
            i = _skip_string(d, i)
            continue
        if c == 0x3C:                      # '<'
            if d[i:i + 2] == b'<<':
                depth += 1
                i += 2
                continue
            i = _skip_hex(d, i)
            continue
        if c == 0x3E:                      # '>'
            if d[i:i + 2] == b'>>':
                depth -= 1
                i += 2
                if depth == 0:
                    return i
                continue
            i += 1
            continue
        i += 1
    return n


def _skip_array(d, i):
    start, depth, n = i, 0, len(d)
    while i < n:
        c = d[i]
        if c == 0x28:
            i = _skip_string(d, i)
            continue
        if c == 0x3C:
            if d[i:i + 2] == b'<<':
                i = _skip_dict(d, i)
                continue
            i = _skip_hex(d, i)
            continue
        if c == 0x5B:
            depth += 1
            i += 1
            continue
        if c == 0x5D:
            depth -= 1
            i += 1
            if depth == 0:
                return i
            continue
        i += 1
    return n


def dget(d, key):
    """Return the raw value bytes for /key inside dict bytes d."""
    if not d:
        return None
    key = key.encode() if isinstance(key, str) else key
    m = re.search(rb'/' + re.escape(key) + rb'(?![A-Za-z0-9#\-+._])', d)
    if not m:
        return None
    i, n = m.end(), len(d)
    while i < n and d[i] in b' \t\r\n':
        i += 1
    if i >= n:
        return None
    c = d[i]
    if c == 0x3C and d[i:i + 2] == b'<<':
        return d[i:_skip_dict(d, i)]
    if c == 0x5B:
        return d[i:_skip_array(d, i)]
    if c == 0x28:
        return d[i:_skip_string(d, i)]
    if c == 0x3C:
        return d[i:_skip_hex(d, i)]
    if c == 0x2F:                            # name value
        j = i + 1
        while j < n and d[j] not in b' \t\r\n[]<>/()':
            j += 1
        return d[i:j]
    j = i
    while j < n and d[j] not in b' \t\r\n[]<>/()':
        j += 1
    tok = d[i:j]
    if re.fullmatch(rb'\d+', tok):                     # extend `154` -> `154 0 R`
        m2 = re.match(rb'[ \t\r\n]+(\d+)[ \t\r\n]+R\b', d[j:j + 24])
        if m2:
            j += m2.end()
    return d[i:j]


def ref_num(v):
    if not v:
        return None
    m = re.match(rb'(\d+)\s+\d+\s+R$', v.strip())
    return int(m.group(1)) if m else None


def name_of(v):
    return v[1:] if v and v.startswith(b'/') else v


# ---------------------------------------------------------------- stream utils
def inflate(data):
    for candidate in (data, data[2:], data[1:]):
        try:
            return zlib.decompress(candidate)
        except Exception:
            pass
    try:
        return zlib.decompressobj(-15).decompress(data)
    except Exception:
        return None


def split_stream(body):
    idx = body.find(b'stream')
    if idx == -1:
        return body, None
    head = body[:idx]
    start = idx + 6
    if body[start:start + 2] == b'\r\n':
        start += 2
    elif body[start:start + 1] in (b'\n', b'\r'):
        start += 1
    end = body.rfind(b'endstream')
    if end < start:
        end = len(body)
    return head, body[start:end]


def scan_objects(raw):
    objs = {}
    for m in OBJ_RE.finditer(raw):
        num = int(m.group(1))
        end = raw.find(b'endobj', m.end())
        objs[num] = raw[m.end():end if end != -1 else len(raw)]
    return objs


def expand_objstms(objs):
    found = {}
    for body in list(objs.values()):
        head, stream = split_stream(body)
        if stream is None or b'/ObjStm' not in head:
            continue
        data = inflate(stream)
        if not data:
            continue
        m = re.match(rb'\s*(\d+)\s+(\d+)', data)
        if not m:
            continue
        n, first = int(m.group(1)), int(m.group(2))
        pairs = re.findall(rb'(\d+)\s+(\d+)', data[:first])
        for i, (onum, off) in enumerate(pairs[:n]):
            onum, off = int(onum), int(off)
            nxt = first + (int(pairs[i + 1][1]) if i + 1 < len(pairs) else len(data) - first)
            found[onum] = data[first + off:nxt]
    objs.update(found)


def obj_stream(objs, num):
    body = objs.get(num)
    if body is None:
        return None, None
    head, stream = split_stream(body)
    if stream is None:
        return head, None
    if b'FlateDecode' in head:
        return head, inflate(stream) or stream
    return head, stream


def get_dict(objs, num):
    return obj_stream(objs, num)[0]


# ------------------------------------------------------------------- ToUnicode
def parse_cmap(data):
    m = {}
    txt = data.decode('latin-1')
    for blk in re.findall(r'beginbfchar(.*?)endbfchar', txt, re.S):
        for src, dst in re.findall(r'<([0-9A-Fa-f\s]+)>\s*<([0-9A-Fa-f\s]+)>', blk):
            try:
                m[int(re.sub(r'\s', '', src), 16)] = bytes.fromhex(
                    re.sub(r'\s', '', dst)).decode('utf-16-be', 'ignore')
            except Exception:
                pass
    for blk in re.findall(r'beginbfrange(.*?)endbfrange', txt, re.S):
        for lo, hi, rest in re.findall(
                r'<([0-9A-Fa-f\s]+)>\s*<([0-9A-Fa-f\s]+)>\s*(<[0-9A-Fa-f\s]*>|\[[^\]]*\])', blk):
            try:
                lo_i = int(re.sub(r'\s', '', lo), 16)
                hi_i = int(re.sub(r'\s', '', hi), 16)
            except Exception:
                continue
            if rest.startswith('['):
                for i, d in enumerate(re.findall(r'<([0-9A-Fa-f\s]*)>', rest)):
                    try:
                        m[lo_i + i] = bytes.fromhex(re.sub(r'\s', '', d)).decode(
                            'utf-16-be', 'ignore')
                    except Exception:
                        pass
            else:
                try:
                    base = int(re.sub(r'\s', '', rest[1:-1]) or '0', 16)
                except Exception:
                    continue
                for i in range(min(hi_i - lo_i + 1, 65536)):
                    try:
                        m[lo_i + i] = chr(base + i)
                    except Exception:
                        pass
    return m


# ---------------------------------------------------------------- content lexer
TOKEN_RE = re.compile(rb"""
    (?P<ws>\s+)
  | (?P<comment>%[^\r\n]*)
  | (?P<hex><[0-9A-Fa-f\s]*>)
  | (?P<lit>\((?:[^()\\]|\\.|\([^()]*\))*\))
  | (?P<name>/[^\s/\[\]<>()%{}]*)
  | (?P<num>[-+]?(?:\d+\.\d*|\.\d+|\d+))
  | (?P<arr_open>\[)
  | (?P<arr_close>\])
  | (?P<dict_open><<)
  | (?P<dict_close>>>)
  | (?P<op>[A-Za-z'"*][A-Za-z0-9'"*]*)
""", re.X | re.S)


def unescape_literal(s):
    out = bytearray()
    i = 0
    while i < len(s):
        c = s[i]
        if c == 0x5C and i + 1 < len(s):
            n = s[i + 1]
            simple = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12,
                      0x28: 0x28, 0x29: 0x29, 0x5C: 0x5C}
            if n in simple:
                out.append(simple[n]); i += 2; continue
            if 0x30 <= n <= 0x37:
                j = 0
                while j < 3 and i + 1 + j < len(s) and 0x30 <= s[i + 1 + j] <= 0x37:
                    j += 1
                out.append(int(s[i + 1:i + 1 + j], 8) & 0xFF)
                i += 1 + j
                continue
            if n in (0x0A, 0x0D):
                i += 2
                continue
            out.append(n); i += 2; continue
        out.append(c); i += 1
    return bytes(out)


def decode_text(raw, font):
    if font is None:
        return raw.decode('latin-1', 'ignore')
    cmap, two = font['cmap'], font['two_byte']
    if two:
        chars = []
        for i in range(0, len(raw) - 1, 2):
            code = (raw[i] << 8) | raw[i + 1]
            chars.append(cmap.get(code, ''))
        return ''.join(chars)
    return ''.join(cmap.get(b, chr(b) if 32 <= b < 127 else '') for b in raw)


def mat_mul(m1, m2):
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return [a1 * a2 + b1 * c2, a1 * b2 + b1 * d2,
            c1 * a2 + d1 * c2, c1 * b2 + d1 * d2,
            e1 * a2 + f1 * c2 + e2, e1 * b2 + f1 * d2 + f2]


def parse_content(data, fonts, page_no):
    runs = []
    stack = []
    tm = [1, 0, 0, 1, 0, 0]
    tlm = [1, 0, 0, 1, 0, 0]
    ctm = [1, 0, 0, 1, 0, 0]
    gstack = []
    leading = 0.0
    font, size = None, 12.0

    def em(ch):
        return 1.0 if unicodedata.east_asian_width(ch) in 'WF' else 0.52

    def show(s):
        nonlocal tm
        if not s:
            return
        txt = decode_text(s, fonts.get(font))
        trm = mat_mul(tm, ctm)
        if txt.strip():
            runs.append({'page': page_no,
                         'x': round(trm[4], 2), 'y': round(trm[5], 2),
                         'size': round(size * (abs(trm[0]) or 1), 2),
                         'font': font, 'text': txt})
        adv = sum(em(c) for c in txt) * size
        tm = [tm[0], tm[1], tm[2], tm[3], tm[4] + adv, tm[5]]

    for m in TOKEN_RE.finditer(data):
        kind, val = m.lastgroup, m.group()
        if kind in ('ws', 'comment', 'dict_open', 'dict_close'):
            continue
        if kind == 'name':
            stack.append(('name', val[1:].decode('latin-1')))
        elif kind == 'num':
            stack.append(('num', float(val)))
        elif kind == 'lit':
            stack.append(('str', unescape_literal(val[1:-1])))
        elif kind == 'hex':
            h = re.sub(rb'\s', b'', val[1:-1])
            stack.append(('str', bytes.fromhex((h + b'0' * (len(h) % 2)).decode('latin-1'))))
        elif kind == 'arr_open':
            stack.append(('mark', '['))
        elif kind == 'arr_close':
            items = []
            while stack and stack[-1][0] != 'mark':
                items.append(stack.pop())
            if stack:
                stack.pop()
            stack.append(('list', items[::-1]))
        else:
            op = val.decode('latin-1')
            if op == 'q':
                gstack.append(list(ctm))
            elif op == 'Q':
                if gstack:
                    ctm = gstack.pop()
            elif op == 'cm' and len(stack) >= 6:
                vals = [v for k, v in stack[-6:] if k == 'num']
                if len(vals) == 6:
                    ctm = mat_mul(vals, ctm)
            elif op == 'BT':
                tm = [1, 0, 0, 1, 0, 0]; tlm = [1, 0, 0, 1, 0, 0]
            elif op == 'Tf' and len(stack) >= 2:
                if stack[-1][0] == 'num':
                    size = stack[-1][1]
                if stack[-2][0] == 'name':
                    font = stack[-2][1]
            elif op == 'TL' and stack and stack[-1][0] == 'num':
                leading = stack[-1][1]
            elif op in ('Td', 'TD') and len(stack) >= 2:
                tx, ty = stack[-2][1], stack[-1][1]
                if op == 'TD':
                    leading = -ty
                tlm = [tlm[0], tlm[1], tlm[2], tlm[3], tlm[4] + tx, tlm[5] + ty]
                tm = list(tlm)
            elif op == 'Tm' and len(stack) >= 6:
                vals = [v for k, v in stack[-6:] if k == 'num']
                if len(vals) == 6:
                    tm = vals; tlm = list(vals)
            elif op == 'T*':
                tlm = [tlm[0], tlm[1], tlm[2], tlm[3], tlm[4], tlm[5] - leading]
                tm = list(tlm)
            elif op in ('Tj', "'", '"'):
                if op != 'Tj':
                    tlm = [tlm[0], tlm[1], tlm[2], tlm[3], tlm[4], tlm[5] - leading]
                    tm = list(tlm)
                if stack and stack[-1][0] == 'str':
                    show(stack[-1][1])
            elif op == 'TJ' and stack and stack[-1][0] == 'list':
                for k, v in stack[-1][1]:
                    if k == 'str':
                        show(v)
                    elif k == 'num':
                        tm = [tm[0], tm[1], tm[2], tm[3],
                              tm[4] - (v / 1000.0) * size, tm[5]]
            stack.clear()
    return runs


# -------------------------------------------------------------------- page tree
def resolve_fonts(objs, resources):
    fonts = {}
    fdict = dget(resources, 'Font') if resources else None
    if not fdict or not fdict.startswith(b'<<'):
        return fonts
    for name, num in re.findall(rb'/([A-Za-z0-9.\-+_]+)\s+(\d+)\s+\d+\s+R', fdict):
        fd = get_dict(objs, int(num))
        if not fd:
            continue
        two_byte = b'/Type0' in fd or b'Identity-H' in fd
        cmap = {}
        tu = ref_num(dget(fd, 'ToUnicode'))
        if tu:
            _, data = obj_stream(objs, tu)
            if data:
                cmap = parse_cmap(data)
        fonts[name.decode('latin-1')] = {'cmap': cmap, 'two_byte': two_byte}
    return fonts


def find_catalog(objs):
    for num in objs:
        head = get_dict(objs, num)
        if head and name_of(dget(head, 'Type')) == b'Catalog':
            return head
    return None


def collect_pages(objs):
    root = find_catalog(objs)
    if root is None:
        return []
    pages = []

    def walk(num, depth=0):
        if num is None or depth > 16:
            return
        head = get_dict(objs, num)
        if not head:
            return
        kids = dget(head, 'Kids')
        if kids and kids.startswith(b'['):
            for k in re.findall(rb'(\d+)\s+\d+\s+R', kids):
                walk(int(k), depth + 1)
        elif name_of(dget(head, 'Type')) in (b'Page', None):
            if b'/Contents' in head:
                pages.append(head)

    walk(ref_num(dget(root, 'Pages')))
    return pages


def page_resources(objs, head, depth=0):
    res = dget(head, 'Resources')
    if res and res.startswith(b'<<'):
        return res
    parent = ref_num(dget(head, 'Parent'))
    if parent is not None and depth < 8:
        ph = get_dict(objs, parent)
        if ph:
            return page_resources(objs, ph, depth + 1)
    return None


def page_content(objs, head):
    c = dget(head, 'Contents')
    if not c:
        return b''
    nums = ([int(x) for x in re.findall(rb'(\d+)\s+\d+\s+R', c)]
            if c.startswith(b'[') else [ref_num(c)])
    chunks = []
    for n in nums:
        if n is None:
            continue
        _, data = obj_stream(objs, n)
        if data:
            chunks.append(data)
    return b'\n'.join(chunks)


# ------------------------------------------------------------------------- main
def extract(path):
    raw = open(path, 'rb').read()
    objs = scan_objects(raw)
    expand_objstms(objs)
    pages = collect_pages(objs)
    runs = []
    for i, head in enumerate(pages, 1):
        fonts = resolve_fonts(objs, page_resources(objs, head))
        data = page_content(objs, head)
        if data:
            runs.extend(parse_content(data, fonts, i))
    return runs, len(pages)


def to_lines(runs, y_tol=3.5):
    pages = {}
    for r in runs:
        pages.setdefault(r['page'], []).append(r)
    out = []
    for p in sorted(pages):
        rs = sorted(pages[p], key=lambda r: (-r['y'], r['x']))
        lines = []
        for r in rs:
            if lines and abs(lines[-1]['y'] - r['y']) <= y_tol:
                lines[-1]['runs'].append(r)
            else:
                lines.append({'y': r['y'], 'runs': [r]})
        for ln in lines:
            ln['runs'].sort(key=lambda r: r['x'])
            ln['text'] = ''.join(x['text'] for x in ln['runs'])
            ln['x'] = ln['runs'][0]['x']
            ln['size'] = max(x['size'] for x in ln['runs'])
            ln['x_end'] = ln['runs'][-1]['x'] + sum(
                (1.0 if unicodedata.east_asian_width(c) in 'WF' else 0.52)
                * ln['runs'][-1]['size'] for c in ln['runs'][-1]['text'])
        out.append((p, lines))
    return out


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    path = sys.argv[1]
    runs, npages = extract(path)
    print(f'pages={npages} runs={len(runs)}')
    if '--json' in sys.argv:
        json.dump(runs, open(sys.argv[sys.argv.index('--json') + 1], 'w',
                             encoding='utf-8'), ensure_ascii=False, indent=1)
    for p, lines in to_lines(runs):
        print(f'===== page {p} =====')
        for ln in lines:
            print(f'{ln["y"]:7.1f} x{ln["x"]:6.1f} s{ln["size"]:5.1f} | {ln["text"]}')
