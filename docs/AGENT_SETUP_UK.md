# SyncForge Agent Kit — Optimized

## Встановлення / заміна попереднього пакета

1. Заверши активну агентну задачу й зафіксуй поточний код у Git. За наявності власних змін в `AGENTS.md` або `.agents/` спочатку збережи їх.
2. Розпакуй ZIP **вмістом у корінь** `D:\dev\file_sync\` (поруч із `src`, `tests`, `SyncForge.spec`), погодившись на заміну однойменних файлів із попереднього комплекту. Не створюй зайву вкладену папку `SyncForge_Agent_Kit_Optimized` у репозиторії.
3. Перевір структуру: `AGENTS.md`, `.agents/rules/{modular-architecture,code-quality}.md`, `.agents/skills/<name>/SKILL.md`.
4. Закоміть нові інструкції в Git. Новий чат у тому самому workspace має бачити їх автоматично.

## Як економиться контекст

- `AGENTS.md` — короткі завжди активні пріоритети.
- `code-quality.md` — `glob` для Python файлів; `modular-architecture.md` — лише за рішенням моделі при архітектурній задачі (`model_decision`).
- П'ять Skills лишилися окремими; на початку агент бачить тільки назву/опис, повний `SKILL.md` читає при активації.
- Немає вимоги щоразу читати всі Skills, всю кодову базу, запускати повний pytest або збирати PyInstaller після кожного редагування.
- Безпечні операції з даними та цільові тести залишилися обов'язковими; економія не замінює перевірку.

## Перевірка в Antigravity

У **Customizations → Skills** (якщо меню є в цій версії) перевір наявність п'яти Skills. У чаті можна написати:

> Diagnose the 8,000-file preview freeze. Use `gui-performance`; inspect only affected modules, measure before/after, and avoid release packaging until requested.

Не проси агента прочитати *всі* Skills — це нівелює економію. Квоти Antigravity залежать також від моделі та виконаних агентних дій, тому точну економію токенів/лімітів за розміром файлів не визначити.

Документація Google: https://antigravity.google/docs/rules/ та https://antigravity.google/docs/skills/.
