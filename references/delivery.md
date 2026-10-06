# 交付、复读证据与续作

成品目录仅保留阅读稿及实际引用的资源。含来源标记的核对稿、结构报告、原图对应关系、复读记录和快照放在独立工作目录，不将这些制作记录混入伴读正文。先完成核对稿的内容与结构检查，再由定稿生成阅读稿；阅读稿不依赖阅读器隐藏注释。以下脚本使用 Python 标准库，不执行教材代码，也不下载外部资源。

## 便携图片与打包

```powershell
python <skill-dir>/scripts/package_notes.py --notes <audit-chapter1.md> <audit-chapter2.md> --sections <section-pack> --reader-copy --out <new-delivery-dir> --manifest <work-dir/portable.json> --zip <delivery.zip>
```

`--out` 必须为空或不存在，manifest 和 ZIP 必须是成品目录外的新文件。不覆盖原稿、不移动源资产。`--zip` 可省略。结构化任务传 `--sections` 以记录来源块和原资产身份；纯 Markdown 可省略，但不能据此获得可靠来源块认证。

产物为目录根部的原名 Markdown 与 `images/001.jpg` 等浅层图片路径。仅复制实际引用的图片，按 SHA-256 合并相同字节，不裁切、重绘或降低分辨率。普通指向图片的本地链接也复制；章节间链接改成同级文件名，保留 URL 的查询与片段。`--reader-copy` 移除独立行的内部 `block_id`、`page_idx` 注释，保留代码示例中的同形文本，其余正文和图注不改写；核对稿不变。manifest 记录 reader_copy、移除数量、核对稿和输出摘要、原引用路径、图片摘要与可匹配的来源资产；相同图片可能对应多个源块，这只是字节身份。省略该参数保留原有带标记打包行为，供内部用途使用。

工具检查生成目录的本地引用；指定 ZIP 时再检查 CRC 和归档内链接解析。这个运行时检查只涉及支持的引用与文件存在，不能证明渲染效果、文件内锚点、翻译或讲解正确。远程图片保留原 URL，报告 `external_images` 与 `self_contained_images=false`，不能称为完全离线交付。缺失来源资产记为 `unavailable_source_assets`，需处理后才能声称来源身份均核对。

支持常见行内 Markdown 链接/图片及 HTML img/src、a/href。引用式 Markdown 链接/图片、Windows 绝对文件 URL、反斜杠路径会要求先转换；未选择的章节和其它本地附件明确报错，不能悄悄丢弃。含括号的路径可用尖括号包裹。不要通过删除链接让打包通过。工具不保证任意 Markdown 扩展语法都可迁移，特殊语法仍须实际查看。

严格覆盖检查针对含标记的定稿核对稿，报告写清该文件路径；无标记阅读稿不再送入块标记检查。阅读稿由打包工具检查最终本地引用和 ZIP，并实际查看目录、段落与图文位置。打包工具只去标记和迁移链接，不会修复碎段或截图堆积，必须在写作时恢复这些关系。正文修订先更新核对稿并重新核对，再生成新的阅读稿；内部工作记录与原包不要求搬进 ZIP。

## 严格结构检查与合法排版调整

```powershell
python <skill-dir>/scripts/check_note_coverage.py --sections <section-pack> --notes <audit-chapter1.md> <audit-chapter2.md> --strict --layout-review <work-dir/layout-review.json> --report <work-dir/coverage.json>
```

没有排版例外时省略 `--layout-review`。`--notes` 按实际章节阅读顺序显式列出，不能依赖目录枚举或字母排序。旧调用保持宽松行为；最终交付使用 `--strict`，此时重复标记、来源乱序及未确认的空直接区域都会令退出码非零。

直接区域指核对稿中一个标记到下一标记之间的正文。相邻标记共享后续内容的机制仍保留；严格模式另检查前一个标记是否为空，避免删空正文后误借下一公式通过。恢复被拆开的段落、目录、列表、表格或跨页续段时，对照原页后记录**完整、连续的源块组**；不能为绕过合并记录而把一个阅读单元写成多个碎段。并非所有相邻标记都能视为组合。仅评论或空白不是正文。非空文本也仍不能证明译文完整。

例外文件采用 `mineru4-layout-review/1`，source_id 必须与范围包一致。下面的 ID、原因和证据只是字段示例，使用时填写真实对照结果：

```json
{
  "format": "mineru4-layout-review/1",
  "source_id": "<source-id>",
  "merged_blocks": [
    {
      "block_ids": ["<source-id>:p10:b4", "<source-id>:p11:b0"],
      "reason": "同一代码块跨页续写，两个标记共同对应完整围栏",
      "evidence": "对照原文件第11、12页，确认末行与首行衔接"
    }
  ],
  "order_adjustments": [
    {
      "source_order": ["<source-id>:p12:b1", "<source-id>:p12:b2"],
      "note_order": ["<source-id>:p12:b2", "<source-id>:p12:b1"],
      "reason": "原页浮动图片的阅读位置与解析块顺序不同",
      "evidence": "原文件第13页显示图注属于前段，已按图文关系复读"
    }
  ]
}
```

合并组必须匹配实际相邻标记组且有后续正文；末尾孤立空标记不能靠合并记录消失。读序调整必须是连续源范围的精确排列，多个调整不能重叠。记录只确认排版关系，不会自动清除材料缺失、needs_review、坏链接或重复标记；也不代替语义复读。仍需读取报告而不是只看退出码。

## 资源异常与原文件对照

`prepare_sections.py` 在 sections.json/issues 提示资源类型冲突、公式夹编程语法、无围栏代码、相邻跨页代码及空资源。这些是有限启发式，会有漏报或误报；没有提示不证明原文件全部内容都被提取。资源异常也进入覆盖报告，便于定位，**不自动判定翻译错误或修改源分类**。

回看原页后确需调整供料分类，可传 `--resource-overrides <json>`：

```json
{
  "<source-id>:p12:b3": {
    "type": "table",
    "reason": "解析为code，但原页为含行列的表格",
    "evidence": "对照原文件第13页的表2及完整表头、数据"
  }
}
```

允许目标类型为 image、chart、equation、code、table，只作用于本次范围的内容块。记录 raw_type 与更正依据，原始 blocks/document/assets 不改写；内容本身也不会由类型修正自动补全。经确认后在新的空目录重新准备。若截图或原文件还有解析文本没有的调用、条件或表格行，直接按真实来源恢复笔记并记录位置和依据，不能把结构检查通过称为原文件完整。

## 完成声明对应哪些证据

工作记录分开保存四类事实，不采用一个 complete 标志代表所有质量：

| 事实 | 必须说明的实际依据 | 不能据此推断 |
|---|---|---|
| 结构覆盖 | 定稿核对稿、范围、严格报告及对应阅读稿的输出记录 | 逐句译文完整或正确 |
| 资源一致 | 图片摘要、资源差异的来源对照、实际查看范围 | 全部图片清晰、所有 OCR 正确 |
| 来源复读 | 对照了哪些小节/页/块，条件、例子、脚注及代码的结果 | 抽查之外内容也已经逐段核对 |
| 讲解复读 | 实际复读的难点，决定性中间步骤与原文结论是否连接 | 所有主题均达到同一深度 |

记录可用简短 Markdown 或 JSON；不用逐段评分，也不需要独立审阅代理。每项记录明确范围、具体处理结果及未解决项。人工确认的 needs_review 可以保留在原始报告中，用复读记录说明实际处理；不能改写报告或把非零状态笼统称为通过。资源异常同理，需要实际来源对照结果。

完整伴读交付要求约定范围逐节对照来源并补齐材料，重点讲解满足已确定的读者路径。只有抽查证据时应如实说明范围，不能扩展成全范围逐段验收；没有运行新版检查时明确待检查。结构计数、字数、已保存快照或打包成功都不能充当质量证明。最终回复仅提供结果、路径和影响使用的剩余问题；内部记录单独保存。

## 中断落盘与恢复

持续多章任务、中断前或用户要求保存进度时使用快照工具。先写简短 progress.json，例如：

```json
{
  "next_block_id": "<source-id>:p20:b2",
  "pending_items": ["本小节最后一段未翻译", "跨页代码须对照原文件"],
  "source_review": {"scope": "已逐节对照第1章；第2章只抽查两节", "record": "source-review.md"},
  "teaching_review": {"topics": ["公式推导中的关键中间步骤"], "record": "teaching-review.md"}
}
```

```powershell
python <skill-dir>/scripts/checkpoint_notes.py create --sections <section-pack> --notes <chapter1.md> <chapter2.md> --progress <work-dir/progress.json> --records <work-dir/coverage.json> <work-dir/source-review.md> <work-dir/teaching-review.md> --out <new-checkpoint-dir>
python <skill-dir>/scripts/checkpoint_notes.py verify --checkpoint <checkpoint-dir> --current
```

未写出后续章时只传已有笔记；`--records` 只传确实存在的记录。next_block_id 必须为本次源范围的完整 ID、排除式结束边界或 null；null 只表示没有指定续写块，不是完成认证。pending_items 始终保留为数组。

快照包含笔记、可识别的本地引用文件、小节索引、资源索引和显式记录的原字节副本及摘要；原来源包记录路径和关键 JSON 摘要，保持原位置，不另复制整本源资产。远程图片不下载。快照的 files 编号用于恢复原路径，**不是可直接浏览的出版物**。源包另有备份需要时按实际任务安排，不能把工作快照宣称为完整来源备份。

续作先读取 checkpoint.json/progress，再核对快照及当前文件摘要。current_work_changed_or_missing 可能是之后正常续写，也可能是替换或丢失，需要查看真实差异；source_changed_or_missing 要重新确认身份和范围。不自动覆盖当前文件；按 checkpoint.json 的 original_path 在确认目标后选择需要恢复的具体文件。只有摘要一致才能沿用该版本的复读记录；内容修订后说明哪些证据需重新核对。
