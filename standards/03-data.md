# 03 数据规范

## 1. 数据落位（两类，泾渭分明）

| 类型 | 位置 | 内容 | 生命周期 |
|---|---|---|---|
| 静态数据 | `assets/` | rules（规则+作品库）、data（角色卡/桥段/名册 JSON）、personas（性格 MD）、prompts（提示词 MD）、lore（世界书 JSON） | 随仓库分发 |
| 运行数据 | `var/` | db（SQLite）、books（切章书库）、saves、sessions、logs、outputs、uploads | 程序自动生成，gitignore |

## 2. 路径派生（禁止硬编码）

```python
# 静态数据：从模块文件位置派生到项目根再进 assets/
CATALOG_DIR = Path(__file__).resolve().parents[2] / "assets" / "data"   # core/engine/*.py 的写法
RULES_PATH  = os.path.join(fe.BASE_DIR, "assets", "rules", name)        # 经 fate_engine.BASE_DIR

# 运行数据：一律经可写根派生
fe.WRITABLE_DIR                                    # = 项目根/var（frozen 时 exe 同级 var/）
os.path.join(fe.WRITABLE_DIR, "books", book_id)    # 切章书库
os.path.join(fe.WRITABLE_DIR, "logs")              # 日志
engine.persistence.save_state(state, root=fe.WRITABLE_DIR)   # 存档：root/saves、root/db 由此派生
Path(__file__).resolve().parents[2] / "var" / "db" / "fate_engine.db"  # SQLite
PROJECT_ROOT / "var" / "uploads"                   # 会话上传（core/api/sessions.py）
```

- **禁止** `Path(__file__).parent.parent / "data"` 这类依赖目录深度的脆弱写法（两次结构整理都翻车于此）；必须写清 `parents[N]` 并加注释。
- 禁止把运行数据写到 `assets/` 或项目根。

## 3. 数据库（var/db/fate_engine.db）

- 单文件多表：`characters`（角色库）与 `saves`（存档）共存。
- 访问层：`core/engine/character_db.py`（角色）、`core/engine/persistence.py`（存档）。禁止绕过访问层直接 sqlite3。
- `character_db.DATABASE_PATH` 与 `persistence._db_path(root=fe.WRITABLE_DIR)` 必须指向同一文件——改任何一个都要核对另一个。
- schema 变更必须配迁移逻辑（`character_repository.migrate_from_json` 先例），不得让旧库打不开。

## 4. 数据 schema 变更纪律

- 角色卡四栏槽位词表：`core/engine/character_designer.py` 的 `SLOT_KEY_VOCAB`（16 词白名单），数据库校验、前端映射、设计器归一化三处共用——改动必须三方同步。
- `role` 枚举：主角/伙伴/single_heroine/multi_heroine/反派/女主；`gender` 三值 male/female/unknown；`original_position` 五值。新增枚举值先改词表常量再改校验。
- 数组字段（knowledge_scope、slot_keys）落库必须是真数组，禁止 `str(list)` 压字符串（存量 bug 已修，勿回退）。
- JSON 数据文件：UTF-8、无 BOM、`ensure_ascii=False`、尾换行。

## 5. books/（切章书库）

- 位置 `var/books/<md5>_<书名>/`，含 `chapter_index.json` + `chapters/NNNN.txt` + `anchors/NNNN.json`。
- 由 `engine.chapter_tools.split_file` 生成；锚点由 `engine.anchor_distiller` 渐进蒸馏。
- 读路径经 `fe.WRITABLE_DIR/books` 派生，禁止绝对路径缓存到 state（state 存 `distill_key` 绝对路径是历史例外，勿扩散）。
