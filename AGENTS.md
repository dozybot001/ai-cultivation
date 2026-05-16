# AI Cultivation Workspace Notes

This workspace contains the local source of truth for the AI 修仙体系 project.

## Public GitHub Content

- `README.md` is the public project entrypoint.
- `docs/` contains the public Markdown documents.
- `docs/full-system.md` is the complete master document.
- `docs/terminology.md` is the naming and terminology authority.

## Local-Only Feishu Publishing

- Feishu metadata lives in `.local/feishu/` and should not be committed.
- Generated Feishu XML lives in `.local/build/` and should not be committed.
- Use `python3 tools/sync-feishu-wiki.py --apply` to sync to Feishu.
- Do not upload public Markdown directly to Feishu; the script converts it to Feishu-safe XML first.

## Naming Discipline

Official terminology uses `AI 修仙境界` mapped to `凡人修仙对照`; do not revive the deprecated `俗世别名` framing.

Use:

- `真身 / 分身`
- `天地五源`
- `法门`
- `初证 / 成式 / 行法 / 圆满`
- `聚材 -> 立则 -> 试法 -> 授令 -> 记功 -> 复炼`
- `咒术宗 / 玉简宗 / 阵法宗 / 分身宗 / 护道宗 / 逍遥宗`

Avoid:

- `天地五行`
- `道脉`
- `入脉`
- `立卷`
- `定则`
- `试炼`
- `小授`
- `留痕`
- `提示宗`
- `知库派`
- `阵法派`
- `逍遥派`
- `俗世别名`
