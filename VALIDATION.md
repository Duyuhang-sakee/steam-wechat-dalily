# 本地验证记录

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
