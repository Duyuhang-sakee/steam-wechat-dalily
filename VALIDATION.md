# 本地验证记录

## 2026-10-13：删除临时测试入口，恢复正式定时日报

哥大确认网页报告已经可以打开，要求删除测试入口并每天早上09:30运行。本次按Refactoring清理：删除Actions的“仅生成预览”“测试重复推送”输入、单独预览步骤、NOTIFICATION_ID和--notification-id参数；定时运行与正常手动运行统一执行准备、部署、核验与推送。

保留每天UTC01:30的cron `30 1 * * *`，对应北京时间09:30。配置上传到默认分支master且工作流启用后生效；无需本机常开。README补充Actions启用和正常手动重试步骤。09:30是计划启动时间，调度、采集及发布会使收到微信的时间延后。

修改文件：
- .github/workflows/daily.yml：移除测试输入、预览步骤和临时发送参数，保留正式手动运行、定时、串行执行和失败产物保存。
- steam_daily/__main__.py：移除新测试编号重建通知的分支和CLI参数，沿用每日去重。
- steam_daily/delivery.py：移除手动编号校验和按编号优先恢复，统一恢复最新可用流水；兼容旧测试状态中的额外元数据。
- tests/test_report.py、tests/test_delivery.py：移除临时重复推送测试，新增旧测试artifact去重与次日采集/发送回归。
- README.md、DESIGN.md、VALIDATION.md：正式运行及定时设置说明，保留此前验证记录作为历史。

实际验证：
- 报告与投递流程针对性回归通过。
- python -X utf8 -m unittest discover -s tests -v：52项全部通过，包含本地真实HTTP链路测试。
- 新回归确认旧测试artifact中的accepted状态、receipt和attempts完整恢复，未产生新的发送；昨日流水不影响今天重新采集和创建新通知，同日再跑仍去重。
- python -X utf8 -m compileall -q steam_daily tests：通过。
- YAML解析与配置核对通过：cron保持北京时间09:30、workflow_dispatch无测试输入、正常步骤无预览条件、先部署后发送、失败仍保存状态和报告。
- IDEA检查修改的生产Python、测试和工作流文件无错误。
- git diff --check：通过，仅Windows换行转换提示。
- Python测试和Git检查通过IDEA内置终端执行，当前项目没有Python运行配置。

本轮未执行真实微信发送、远程部署、提交或推送；已有发送记录和远端产物未修改。以下记录描述此前阶段，包含本次已经删除的临时测试功能。

## 2026-10-13：手动测试允许重复推送

哥大明确要求测试阶段解除同一天只能发一条的限制。本次按Small Feature实现：新的手动Run workflow默认准备并提交一个页面入口，可以当天重复；复用经过绑定验证的当天快照。同一运行的Re-run jobs恢复原发送记录，自动日报及关闭测试选项的手动运行沿用每日去重。

修改文件：
- .github/workflows/daily.yml：默认开启resend_notification输入，手动测试用github.run_id作为NOTIFICATION_ID，传入准备阶段。
- steam_daily/__main__.py：支持--notification-id，新编号重建待发送入口并解除旧suppressed，保留被替换的上一条记录；相同编号保留原流水。
- steam_daily/delivery.py：校验通知编号，恢复时优先取同一次手动运行之前attempt的状态产物。
- tests/test_report.py：真实本地HTTP验证两个新编号产生两个POST，相同编号重试不新增POST；覆盖旧摘要、不同投递状态、旧分页抑制及非法参数。
- tests/test_delivery.py：在其他运行产物更新的情况下，优先恢复当前手动运行的流水。
- README.md、DESIGN.md、VALIDATION.md：手动重复推送入口、重试语义及验证记录。

实际验证：
- 实现前针对性测试出现9处失败及1处错误，检出原流程无法准备新的手动通知。
- 实现后报告/流水回归21项通过，投递/HTTP回归18项通过。
- python -X utf8 -m unittest discover -s tests -v：54项全部通过。
- python -X utf8 -m compileall -q steam_daily tests：通过。
- 工作流YAML解析及配置核对：通过；测试输入为boolean、默认开启，通知编号只由手动测试提供，预览仍默认开启。
- IDEA检查修改的生产Python和工作流文件无错误。
- git diff --check：通过，仅Windows换行转换提示。
- Python测试与Git检查通过IDEA内置终端执行，当前IDEA项目没有Python运行配置。

测试使用本地HTTP服务器及虚拟凭证；未执行真实微信推送、远程部署、提交或推送。使用时上传新版，再点新的Run workflow，取消“仅生成预览”，保留“测试重复推送”。生产代码仅使用标准库；YAML核对使用环境已有解析库。

## 2026-10-13：同日重跑日志修正

线上诊断：运行 [Steam 微信晨报 #7](https://github.com/Duyuhang-sakee/steam-wechat-dalily/actions/runs/38339765397) 的 digest 用时41秒。准备日志显示恢复当天快照；发送正文没有实际发送后的 Page 状态日志，仅有旧版固定的“PushPlus accepted”输出。结合未配置 PUSHPLUS_SECRET_KEY 和投递路径，确认本次复用了当天已接收记录，跳过新POST，不是一次新的全站采集。摘要页有3个产物：github-pages、steam-daily-report-38339765397-1、steam-daily-state-38339765397-1。

修正范围：
- steam_daily/__main__.py：恢复日志显示原采集时间和本次未重新扫描；根据实际尝试次数变化区分新提交与去重跳过，另行说明最终送达是否已验证。
- tests/test_report.py：真实本地HTTP首次提交及同日重跑的请求次数与日志断言；已接收、待确认、已送达和确认失败后重试的日志回归。
- README.md：同日快速重跑的原因、成功状态的含义及产物入口。
- VALIDATION.md：本次诊断与验证记录。

实际验证：
- 修改生产代码前，针对性回归出现7处断言失败（含5个子场景），检出旧日志不能区分提交与跳过。
- 修正后 python -X utf8 -m unittest discover -s tests -p test_report.py -v：18项通过。
- python -X utf8 -m unittest discover -s tests -v：50项通过。
- python -X utf8 -m compileall -q steam_daily tests：通过。
- IDEA错误检查：两个修改的Python文件均无错误。
- git diff --check：通过，仅Windows换行转换提示。上述Python检查及Git差异检查通过IDEA内置终端执行，项目没有Python运行配置。

未修改每日去重或失败重试规则；未清除历史记录、真实补发微信、提交或推送。本次日志只能确认此前平台接收及本次跳过，不能证明此前微信最终送达。

## 2026-10-13：微信只推送完整页面入口

任务：去掉微信商品摘要，以一个完整网页链接通知哥大查看带封面、搜索和筛选的优惠目录。

实际通过：
- python -X utf8 -m unittest discover -s tests -v：49项通过。
- python -X utf8 -m compileall -q steam_daily tests：通过。
- IDEA build_project：成功；IDEA对Python文件及工作流的错误检查无报错。项目在IDEA中为Java模块且无Python运行配置，Python测试与编译通过IDEA内置终端执行。
- git diff --check：通过，仅有Windows换行转换提示。

回归测试已先检出旧行为，再确认修正：
- 准备、公开manifest核验、投递通过真实Http和本地HTTP服务器运行；请求正文仅有一个完整网页链接，没有商品名或折扣摘要，同日重跑仅一次POST。
- 旧摘要从未发送或已明确失败时替换为链接，快照、尝试次数、状态和流水号不丢失。已接收、待确认、已送达、发送中或结果不明时保持旧记录。
- 旧摘要先被接收、随后查询确认投递失败的分支，实际重试发送新页面链接；原问题在新增回归测试中可复现。
- 发布地址或公开快照不匹配时停止发送；原有全量网页、封面、Steam样例、ZIP恢复和旧分页抑制测试继续通过。
- 首次默认Windows编码执行有3项GBK解码错误，属于原测试环境问题；开启Python UTF-8模式后通过。

本次修改文件：
- steam_daily/report.py：页面入口替代摘要，删除旧摘要渲染。
- steam_daily/__main__.py：入口预览及准备流程。
- steam_daily/delivery.py：在实际发送边界迁移可重发的旧正文。
- steam_daily/templates/site.html：更新微信通知文案。
- .github/workflows/daily.yml、config.json：入口命名及移除摘要长度配置。
- tests/test_report.py、tests/test_delivery.py、tests/test_steam.py：请求正文、迁移与重试回归，移除旧摘要断言。
- README.md、DESIGN.md、VALIDATION.md：使用方法、平台限制和验证记录。

普通PushPlus微信接口未提供文档化的自定义通知跳转URL。本次使用html模板发送可点击的页面链接，可能先经过PushPlus消息页再打开网页，未实现通知卡片一步直达Pages。未执行真实微信发送、远程部署、提交或推送；不以本地HTTP测试宣称线上界面或最终送达已验证。

工作区另有.idea/misc.xml的MavenRunner设置差异，本次未主动编辑或恢复该文件。

## 历史记录：2026-10-12 单条摘要与封面网页

验证日期：2026-10-12。Windows，Python3.12.14。记录实际执行结果，不表示新版已经部署或微信已经收到。

## 本次通过

- Python unittest：**47项全部通过**，保留原有Steam和HTTP投递集成测试，增加网页、摘要及发布状态测试。
- Python compileall：通过。
- Node --check：网页JavaScript语法检查通过。
- 工作流YAML解析：通过。核对单一每日UTC01:30计划、预览默认开启、Pages所需权限、发布步骤在发送前、默认成功条件、状态和快照保存；预览不发布、不发送，不自动开启Pages或提交推送。
- 6,000条测试商品在一个HTML的数据中全部保留，无重复或截断；恶意结束script标签和HTML名称经过转义，JSON往返恢复原名称。
- 摘要长度受上限约束，即使100条超长名称限免也只输出一条，溢出项引导至完整网页。
- 同日prepare恢复原快照，不重新扫描；快照被改动时停止；ZIP恢复验证数据绑定。
- 旧版43页状态在已有发送尝试后被转换为当天抑制新通知，停止余下分页；旧分页状态禁止直接发送。
- 发布地址不匹配、公开版本不正确时，不调用PushPlus；相同日报重跑仅调用一次发送请求。
- 实际Steam搜索fixture中的100项封面地址及中文评测被读取；英语评测与缺图路径已验证。不安全的图片URL被拒绝。

IDEA MCP目录读取长时间未返回，终止后按哥大允许的失败回退规则使用本地工具。没有操作其他IDEA项目。

## 实际网页预览

使用已保存的 **2026-10-12 14:54:07，北京时间** 全站价格快照；没有重新遍历全站。预览中有 **6项限免＋5,766项折扣商品＝5,772项**，全部保留。

从真实搜索fixture补充匹配项的图片、评测元数据，并从首次展示的少量实际Steam详情页读取og:image，合计81项有已观察封面。历史快照缺图的其他条目使用占位图；新版每日扫描会直接获取搜索结果的封面。预览不是一次新的全站价格检查。

- 一个网页：24项/页，共241页；首屏24个封面实际加载成功。
- 一个摘要：包括HTML文档壳约1,902字符，低于18,000字符上限；本地预览不含尚未发布的外链。
- 浏览器验证：精选为5项；不存在名称显示空结果；下一页显示2/241；最低折扣90%筛出781项，与快照独立统计一致；搜索Pony Island结果1项；价格升序正常。
- 桌面默认视口四列，手机390×844视口两列；测量可视宽度与页面滚动宽度相等，无横向溢出。
- 浏览器没有JavaScript错误；保存桌面、手机截图。

## 本次改动文件

- .github/workflows/daily.yml：Pages发布先于微信发送。
- steam_daily/__main__.py：准备、发布验证与发送分步，旧状态迁移。
- steam_daily/steam.py：封面与评测元数据。
- steam_daily/report.py：单条摘要、完整静态网页、快照数据校验。
- steam_daily/delivery.py：单条状态与绑定快照恢复。
- steam_daily/templates/site.html、site.css、site.js：响应式商品目录。
- tests/test_report.py、test_steam.py：新行为验证与替换旧分页测试。
- README.md、DESIGN.md、VALIDATION.md：更新与验证说明。

保留本地 .idea/ 目录，未提交、推送、修改远程设置或执行真实微信发送。

## 线上状态与限制

本次检查默认Pages地址 https://duyuhang-sakee.github.io/steam-wechat-dalily/ 返回HTTP404，未上线新版。需要哥大上传更新并在Settings → Pages选择GitHub Actions，然后手动运行部署。

之前云端运行38233005608曾成功生成旧版43页预览，发送步骤跳过；这只能证明旧版预览。哥大后续截图显示旧版分页微信通知，不代表新版已经验证送达。

没有真实PushPlus凭证，未执行新版微信投递；没有执行新版GitHub Pages云端部署。本地模拟和浏览器预览不能替代这两项最终验证。Pages缓存尚未同步时版本检查会停止发送，之后可重跑。严格恰好一次仍受跨系统崩溃与artifact保存失败窗口限制。
