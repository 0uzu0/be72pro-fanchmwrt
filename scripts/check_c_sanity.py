#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C 源码机械审查（无编译器环境下的替代手段）。

针对 fwxd 扩展文件最容易踩的三类编译期错误：

  1. implicit-function-declaration
     调用点早于定义/声明（build-23、build-26 两次构建失败的真凶）
  2. 标识符未定义
     宏/变量被引用但本文件没有 #define 或声明（大文件按行号改写时误删段落）
  3. 结构性损坏
     花括号/圆括号不平衡（段删除越界、替换截断）

不做真正的语义分析，只做"会被 -Werror 判死"的那部分机械核对。
用法: python3 check_c_sanity.py <file.c> [...]
"""

import re
import sys

# 外部 API 白名单：这些符号来自头文件/其他编译单元，本文件不定义也不声明。
# 名单按"出现即合法"处理，逐项都能在 patches/fwxd 或 fwxd/src 的既有头文件里找到出处。
EXTERNAL = set("""
json_object_new_object json_object_new_array json_object_new_int json_object_new_int64
json_object_new_string json_object_new_boolean json_object_object_get
json_object_object_get_ex json_object_object_add json_object_array_add
json_object_array_get_idx json_object_array_length json_object_get_int
json_object_get_int64 json_object_get_string json_object_is_type json_object_put
json_object_from_file json_object_to_json_string json_object_to_json_string_ext
uci_alloc_context uci_free_context uci_lookup_ptr
fwx_uci_get_value fwx_uci_get_int_value fwx_uci_get_list_value fwx_uci_get_list_num
fwx_uci_add_section fwx_uci_add_list fwx_uci_del_list fwx_uci_delete fwx_uci_commit
fwx_uci_set_int_value fwx_uci_set_value
get_app_name_by_id get_client_data_base_dir get_global_traffic_stats get_today_stat
fwx_gen_api_response_data
exec_shell system popen pclose
time localtime localtime_r mktime strftime strptime difftime
strlen strcmp strncmp strcpy strncpy strcat strncat strchr strrchr strstr strtok
strtok_r strdup strndup snprintf vsnprintf sprintf sscanf atoi atol atoll
strtol strtoul strtoull toupper tolower isxdigit isdigit isspace isalpha
memset memcpy memmove memcmp malloc calloc realloc free
fopen fclose fgets fread fwrite fseek ftell fflush fprintf printf vfprintf
perror remove rename mkdir rmdir access unlink stat lstat opendir readdir closedir
open close read write ioctl
qsort bsearch
basename dirname
inet_ntop inet_pton htonl ntohl htons ntohs
LOG_ERROR LOG_WARN LOG_INFO LOG_DEBUG LOG_TRACE
list_for_each_entry list_for_each_entry_safe INIT_LIST_HEAD list_add list_add_tail
list_del list_empty container_of
errno json_object_get_string_len json_object_get_int64 json_object_array_length json_object_array_put_idx sizeof __attribute__ typeof offsetof
""".split())

# C 关键字与类型名（出现在表达式位置时不算未声明）
KEYWORDS = set("""
if else for while do switch case default break continue return goto
struct union enum typedef static const volatile extern inline register auto
void char short int long float double signed unsigned
void_ _Bool size_t ssize_t time_t FILE
""".split())


def strip_code(src):
    """去注释与字符串/字符字面量，保留换行以便行号对齐。"""
    out = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == '/' and i + 1 < n and src[i + 1] == '*':
            j = src.find('*/', i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r'[^\n]', ' ', src[i:j]))
            i = j
        elif c == '/' and i + 1 < n and src[i + 1] == '/':
            j = src.find('\n', i)
            j = n if j < 0 else j
            out.append(' ' * (j - i))
            i = j
        elif c in '"\'':
            quote = c
            j = i + 1
            while j < n:
                if src[j] == '\\':
                    j += 2
                    continue
                if src[j] == quote:
                    j += 1
                    break
                j += 1
            out.append(' ' * (j - i))
            i = j
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def line_of(text, pos):
    return text.count('\n', 0, pos) + 1


def find_functions(code):
    """返回 [(name, start_idx, body_start, body_end, def_line)]，只认列 0 开始的顶层定义。"""
    funcs = []
    pat = re.compile(
        r'^(?:static\s+)?(?:const\s+)?'
        r'(?:struct\s+\w+\s*\*|unsigned\s+\w+|long\s+long|char\s*\*|void|int|size_t|time_t|FILE\s*\*)'
        r'\s*\**\s*(\w+)\s*\([^;{}]*\)\s*\{',
        re.M)
    for m in pat.finditer(code):
        name = m.group(1)
        if name in KEYWORDS:
            continue
        # 扫到配对的 }
        depth = 0
        i = m.end() - 1
        end = None
        while i < len(code):
            if code[i] == '{':
                depth += 1
            elif code[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i
                    break
            i += 1
        if end is None:
            funcs.append((name, m.start(), m.end(), None, line_of(code, m.start())))
        else:
            funcs.append((name, m.start(), m.end(), end, line_of(code, m.start())))
    return funcs


def main(paths):
    rc = 0
    for path in paths:
        with open(path, encoding='utf-8', errors='replace') as f:
            raw = f.read()
        code = strip_code(raw)
        problems = []

        # 1) 括号平衡（整文件）
        for open_c, close_c in (('{', '}'), ('(', ')'), ('[', ']')):
            n_open = code.count(open_c)
            n_close = code.count(close_c)
            if n_open != n_close:
                problems.append("括号不平衡: %s=%d %s=%d" % (open_c, n_open, close_c, n_close))

        funcs = find_functions(code)

        # 文件级全局变量（列 0 定义），供"赋值给未声明标识符"检查使用
        globals_ = set()
        for m in re.finditer(
                r'^(?:static\s+)?(?:struct\s+\w+|unsigned\s+\w+|long\s+long|long|[A-Za-z_]\w*_t|char|int|'
                r'size_t|time_t|FILE|client_node_t|user_record_t|visit_info_t|'
                r'traffic_stat_t|daily_hourly_stat_t)\s*\**\s*(\w+)\s*(?:\[[^\]]*\])?\s*[=;]',
                code, re.M):
            globals_.add(m.group(1))

        # 2) 宏必须先定义后使用
        macro_def = {}
        for m in re.finditer(r'^#\s*define\s+(\w+)', code, re.M):
            macro_def.setdefault(m.group(1), line_of(code, m.start()))
        for name, dline in macro_def.items():
            # 逐处比较位置（只看非 # 开头的使用行）
            for m in re.finditer(r'^(?!\s*#).*\b' + re.escape(name) + r'\b.*$', code, re.M):
                use_line = line_of(code, m.start())
                if use_line < dline:
                    problems.append("宏先用后定义: %s 行%d（定义在行%d）" % (name, use_line, dline))
                    break

        # 3) 函数调用必须早于有定义或有前向声明
        decls = set()
        for m in re.finditer(
                r'^(?:static\s+)?(?:const\s+)?(?:struct\s+\w+\s*\*|unsigned\s+\w+|long\s+long|'
                r'char\s*\*|void|int|size_t|time_t)\s*\**\s*(\w+)\s*\([^;{}]*\)\s*;\s*$', code, re.M):
            decls.add(m.group(1))
        def_lines = {}
        for name, s_idx, b_start, b_end, dline in funcs:
            def_lines.setdefault(name, dline)

        for name, dline in def_lines.items():
            if name in decls or name in EXTERNAL:
                continue
            first_call = None
            for m in re.finditer(r'\b' + re.escape(name) + r'\s*\(', code):
                cl = line_of(code, m.start())
                if cl < dline:
                    first_call = cl
                    break
            if first_call:
                problems.append("无声明且先用后定义: %s() 行%d 调用，定义在行%d"
                                % (name, first_call, dline))

        # 4) 未定义的结构体成员式调用（拼写错的 json_object_xxx 之类）
        for m in re.finditer(r'\b(json_\w+|uci_\w+|fwx_uci_\w+)\s*\(', code):
            sym = m.group(1)
            if sym not in EXTERNAL and sym not in def_lines and sym not in decls:
                problems.append("可疑库调用（白名单里没有）: %s() 行%d"
                                % (sym, line_of(code, m.start())))

        # 5) 赋值给未声明的标识符
        for name, s_idx, b_start, b_end, dline in funcs:
            if b_end is None:
                problems.append("%s(): 花括号不闭合（行%d 起）" % (name, dline))
                continue
            body = code[b_start:b_end]
            body_base_line = line_of(code, b_start)
            locals_ = set()
            for m in re.finditer(
                    r'\b(?:struct\s+\w+|unsigned\s+\w+|long\s+long|long|[A-Za-z_]\w*_t|char|int|void|size_t|time_t|'
                    r'FILE|client_node_t|user_record_t|visit_info_t|traffic_stat_t|'
                    r'daily_hourly_stat_t|struct\s+json_object|struct\s+uci_context|'
                    r'[A-Za-z_]\w*_t)'
                    r'\s*\**\s*(\w+)', body):
                locals_.add(m.group(1))
                # 多变量声明（int a, b; char *p, q;）—— 声明行里逗号分隔的
                # 后继变量名也算已声明（build-51 的 'int id, got;' 误报教训）
                line_end = body.find('\n', m.start())
                if line_end == -1:
                    line_end = len(body)
                for nm in re.findall(r'(?:\*|\s)\s*([a-zA-Z_]\w*)\s*(?=[,;\[=)])',
                                     body[m.start():line_end]):
                    locals_.add(nm)
            # 函数参数
            for m in re.finditer(r'\b(\w+)\s*(?:,|\))', code[s_idx:b_start]):
                locals_.add(m.group(1))
            for m in re.finditer(r'\b([a-z_]\w*)\s*=(?!=)', body):
                lhs = m.group(1)
                # 结构体成员赋值（->x = / .x =）不是标识符声明问题
                if m.start() >= 2 and body[m.start() - 2:m.start()] in ('->', '.'):
                    continue
                if m.start() >= 1 and body[m.start() - 1] == '.':
                    continue
                if lhs in locals_ or lhs in globals_ or lhs in EXTERNAL or lhs in KEYWORDS:
                    continue
                problems.append("%s(): 赋值给未声明标识符 %s（约行%d）"
                                % (name, lhs, body_base_line + body.count('\n', 0, m.start())))

        # 输出
        rel = path.replace('\\', '/').split('/')[-1]
        if problems:
            rc = 1
            print("[x] %s —— %d 个问题" % (rel, len(problems)))
            for p in problems[:40]:
                print("      " + p)
        else:
            print("[v] %s —— 干净（%d 个函数）" % (rel, len(funcs)))
    return rc


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("usage: check_c_sanity.py <file.c> ...")
        sys.exit(2)
    sys.exit(main(sys.argv[1:]))
