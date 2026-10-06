# 新版数据与提取产物

支持 `pages: [{page_idx, blocks}]`。Structured Content 由标准库直接读取；MiddleJson 经 MinerU 4 `ParseResult.from_dict(...).structured_content()` 规范化。`docvortex.model` 不是 MiddleJson，拒绝猜测转换。旧列表结构亦拒绝。

页必须按原始 page_idx 递增且唯一，块按输入顺序处理，绝不按类型或 bbox 重排。空白页仍计数。`metadata.document.page_count` 可能为全本页数，实际选择范围看 pages 与 is_full_document。

manifest 标识文档 `source_id`；块、标题与资源通过 `block_id` 关联（源 JSON SHA-256 前缀 + 页号 + 块序号）。原始 document.json 不额外注入这些字段：

| 文件 | 用途 |
|---|---|
| manifest.json | 页数、原页号、块数、类型统计、完整性、警告 |
| document.json | 未改写的规范化 Structured Content，保留 metadata/extensions |
| blocks.json | 原块字段加来源定位、section_id，包含页眉脚等全部块 |
| headings.json | 候选标题树、推断依据、页范围、父子关系 |
| assets.json / assets/ | 图片路径、类型、图注、所属块；含表格和公式截图 |
| tables.json | 原始表格正文，可能是 HTML 或 Markdown，不压缩代码式空白 |
| code.json | 原始代码块（保留围栏及语言标签） |
| equations.json | 公式正文与图片来源 |
| reading.md | 按源顺序的阅读视图、来源标记和图片链接 |

图像路径还会从正文 HTML img src / Markdown 图片引用中收集。本地引用必须存在且位于输入根目录；外部 URL 保留并记录，不自动下载。引用路径包含空格时，阅读视图用 URL 编码的相对路径。对于纯 MiddleJson 且 SDK 序列化已省略图像素材的输入，脚本不能恢复不存在的图片；优先使用完整 ZIP。

标题规则是辅助推断，不是原文真值：编号章节优先；点分数字用编号深度；非编号标题按 level 或保守默认层级。所有推断依据写入 headings，非编号标题产生人工核验提示。完整数据保留，后续可重建树。

PDF、DOCX、PPTX 统一通过新版结构读取；文档类型不保证标题推断准确，仍按实际来源确认标题、图像及专属内容。

## 按小节准备生成输入

`prepare_sections.py --pack <existing-pack> --start <block-id> --end <exclusive-block-id> --out <new-dir>` 仅读取现有包，不改源文件、不重新解析PDF。省略 end 表示到文档末尾。边界必须存在且按源顺序；输出目录必须为空或不存在。脚本沿源块遍历，不依页眉切节。

- `sections.json`：格式 `mineru4-section-pack/1`；含来源包相对路径、首尾边界、sections（标题/层级/父级/祖先/块列表/页号/文件）、expected、excluded、corrections、issues。expected 的 type 为供料使用的类型，raw_type 保留原类型；旧包只有 type 时仍可检查。
- `sections/001.md` 等：按源顺序的连续语义段，保留内容、资源、图注、脚注及 `<!-- block_id=... -->`。文件编号为本次范围中的有序编号，不作为跨范围身份；稳定身份始终是 block_id。
- `resources.json`：每个源资源块一条记录，包含 type、raw_type、公式文本与相关资产、section_title、display_label、context_before/after。邻近上下文只取当前连续小节中的正文，不跨界猜测。相似资源不会自动合并；跨页归属需读者或代理判断。

供料图片仍引用源包 assets，不复制整本图像。小节 Markdown 使用自身目录的路径基准。可选 `--notes-dir <writing-dir>` 不改变该阅读视图，而是在 sections.json 增加 notes_directory（相对小节包目录），在每条资源中增加 note_images：source_path、path、markdown。其中 path/markdown 按写作目录生成，涵盖该块全部资产；不自动创建笔记目录、不改源资产。手工换目录应重新计算引用；默认便携交付由 package_notes.py 复制图片并重写浅层路径。省略参数时需自行重算写作引用。Windows 不同盘符无法计算相对路径时明确报错，在写出供料前停止；可选择同盘目录或另行安排资产，不能伪造相对链接。

重复标题只有同时满足“已出现、当前祖先、页顶部、另有 header 类型的同名证据”才自动按页眉处理，记录修正依据。其它重复标题保留并报告疑点；可传 `--overrides <json>`，用完整 block_id 映射到 `heading` 或 `running_header`。原始块不删除；不要将此启发式当成所有文档的标题真值。

独立 page_footnote 保留在源顺序位置，并提示其归属仍是页级，不静默绑定到刚出现的小节；块内 footnotes 跟随该块保留。疑点边界应在生成前确认。

资源分类/语法冲突、混合代码与公式、无围栏代码、跨页代码及空资源以 `resource_*` issue 提示，不能据此证明提取完整性。`--resource-overrides` 是独立 JSON：完整内容块 ID 映射到 type、reason、evidence，目标限于 image/chart/equation/code/table。仅影响供料渲染和资源记录，保留原类型与依据，不改原始内容；具体例子见 [交付与续作指南](delivery.md)。

## 输出标记与覆盖报告

内部核对稿在来源内容前保留规范标记 `<!-- block_id=完整编号 -->`；下一标记之前的内容为其直接区域。恢复自然段、列表、表格或跨页续段时可连续放置多个标记，共享后续材料；严格模式需要已对照来源的合并记录，标记边界不决定阅读排版。新增解释不另造来源编号。最终阅读稿通过 `package_notes.py --reader-copy` 移除独立行的内部来源标记，必要来源定位使用页码或章节引用；核对稿与报告另存。

`check_note_coverage.py --sections <section-pack> --notes <explicit-md-paths> --strict --report <json>` 按参数中的笔记阅读顺序检查，报告 missing、needs_review、broken_links、out_of_scope、duplicate_markers、source_order_issues、empty_direct_bodies、adjacent_merges、asset_matches 及 resource_anomalies。仅传实际笔记，不传 README/覆盖记录。缺项、需复读、坏链接或越界返回非零；严格模式另对重复、乱序及未确认空段返回非零，check_passed 只对应本次机械检查。旧调用省略 `--strict` 时保留宽松行为，后三种异常仍显示但不独立改变退出码。

可选 `--layout-review` 使用来源绑定的 `mineru4-layout-review/1`，包含具有 reason/evidence 的明确合并组和精确读序排列；不能以一条笼统的“已复核”豁免整章。例外结构与限制见交付指南。结构检查支持常见行内 Markdown 链接和 HTML img，不覆盖任意扩展语法或文件内锚点；打包工具另处理 HTML a 链接，其他引用仍需实际核对。

公式识别支持 `$$...$$` 和 `\[...\]`，代码支持三反引号围栏，表格支持常见 Markdown/HTML。支持匹配源截图及与源图 SHA-256 相同的副本、保留的完整公式/代码/表格；表达形式变化则需复读，不做数学等价证明。asset_matches 记录来源/笔记路径、摘要与匹配方法，原资源索引无须改成副本路径。机械检查不验证翻译忠实度、解题正确性或图像视觉效果，无法代替小节复读。

## 便携目录与工作快照

package_notes.py 的内部 manifest 使用 `mineru4-portable-notes/1`；保留笔记输入/输出摘要、reader_copy、每份笔记移除的内部元数据数量、实际引用的图片摘要与来源身份、外部图片及 ZIP 核对结果，独立于成品目录。checkpoint_notes.py 的 checkpoint.json 使用 `mineru4-work-checkpoint/1`；记录来源范围、续写位置、待办、笔记/记录副本与摘要、原来源包的关键文件摘要。两者均不改源包，不生成质量评分或自动完成认证。具体命令、恢复方式及支持边界见 [交付与续作指南](delivery.md)。
