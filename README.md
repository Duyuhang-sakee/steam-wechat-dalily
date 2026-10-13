# Steam 微信每日晨报

自动日报每天北京时间 **09:30** 开始检查一次 Steam 国区优惠，发送 **一个完整优惠页面链接**；手动测试支持当天重复推送。打开页面后查看封面卡片、限免和全部优惠。全部五折及以上商品放在带封面的 GitHub Pages 网页，支持搜索、筛选、排序和分页，不再拆成几十条消息。

09:30 是任务计划开始时间，采集、网页发布和 GitHub 调度会带来延迟，并非微信准点到账保证。没有每十分钟扫描，也没有单独的截止前任务。

## 更新已有仓库

1. 将新版完整项目文件覆盖到本地项目，再由哥大提交并上传到 GitHub。仓库根目录必须能看到 steam_daily/、tests/ 和 config.json；确认隐藏目录 .github/workflows/daily.yml 也已更新。上传的是解压后的项目文件，压缩包用于下载。
2. 打开仓库 **Settings → Pages → Build and deployment → Source**，选择 **GitHub Actions**。尚未设置时，工作流会在读取 Pages 配置时停止；程序不会自行修改仓库设置。
3. 保留现有 Actions Secret **PUSHPLUS_TOKEN**，无需重新填写，也不需要提供给聊天。可选 PUSHPLUS_SECRET_KEY 仍沿用。
4. 在 **Actions → Steam 微信晨报 → Run workflow** 先保留“仅生成网页和微信页面入口预览”选项。报告 ZIP 中 **site/index.html** 是完整网页，**wechat-notification.html** 是页面入口消息预览，点击即可打开同目录的完整网页；预览不会发布或发微信。
5. 检查后取消“仅生成预览”，保留默认开启的 **“测试重复推送”**，点击 **Run workflow**。每次新的手动运行都会发送一条页面入口，当天已接收过旧摘要或页面通知也可再次测试；同日复用已核验的快照，不必重新遍历 Steam。同一次运行的 **Re-run jobs** 按原流水重试，已接收的通知仍跳过；需要再收一条时点击新的 Run workflow。工作流先部署 Pages，核验线上网页确为这份数据，再发送页面入口通知。入口可在 **Settings → Pages → Visit site** 查看；消息链接自动采用实际 Pages 地址，支持自定义域名。
6. 之后按每天北京时间 09:30 自动运行，Windows 电脑无需常开。Pages 页面每天覆盖为最新快照，旧微信链接也打开最新名单，并提示日期变化。

**升级当天：** 自动日报或关闭“测试重复推送”的手动运行，若旧版状态中已有任何消息发送尝试，会停止旧版未发分页，当天仅更新网页、不额外发通知，次日恢复每日一条。开启“测试重复推送”的新手动运行可当天发送新版页面入口，旧分页不会继续发送。如果旧任务仍在运行，它使用的是旧代码，需要在 Actions 中取消该次运行；上传新版不会改变已经开始执行的任务。

当前仓库默认分支为 master。运行新版工作流应选择默认分支；若 Pages 的 github-pages 环境配置了部署分支限制，也应允许该分支。

## 网页与精选规则

- 完整遍历所有公开国区优惠搜索分页，筛选折扣 ≥50%；包含游戏、DLC、软件、礼包、捆绑包等，不只截取热门商品。网页每页展示 24 项，全部结果仍在页面数据和 offers.json 中。
- 封面直接读取 Steam 搜索结果中的实际 HTTPS 图片地址，不猜测 CDN 路径，不为封面逐个抓取数千个详情页。图片缺失或加载失败时显示占位图。搜索小封面可能清晰度有限。
- 折扣精选最多 5 项：优先好评率 ≥80%、评测量 ≥500 的商品，按评测量降序，再以折扣降序、价格升序补足。评测不足不编造评分；精选可能包含 DLC 或软件，不代表个性化推荐。评测来自同一搜索结果，不增加额外全站请求。
- 100% 折扣且价格为零的候选需在详情页核验限时永久领取；排除免费周末、长期免费、Demo 和无国区公开售价的条目。
- 可靠领取截止时间换算北京时间，并标出未来 24 小时内结束的限免。无时区的原文保留、注明未核实时区。公告发布时间不当作活动开始时间，多数普通折扣的起止时间仍未公开。
- 微信正文只有完整网页入口，全部限免和折扣商品在网页展示；商品数量与名称长度不增加微信消息数量。
- 自动日报每天只检查一次，短活动可能在两次检查之间错过。价格、领取条件及需要的本体以 Steam 商品页为准；不自动登录、领取或购买。

## 发布、去重与失败

工作流顺序：测试 → 读取 Pages 地址 → 采集或恢复当天快照 → 生成网页及页面入口通知 → 上传 Pages 文件 → 部署 → 核验公开 manifest.json 的数据哈希 → 发送微信。发布失败、地址不匹配或线上数据未更新时不发消息。Pages CDN 尚未同步时停止，稍后手动重跑即可。

状态 artifact 保存 state.json 和与它绑定的 offers.json。同一天复用原始数据，不重新采集；若原数据被改动或缺失则停止。自动日报、关闭测试重复推送、同一次手动运行的重试沿用发送记录。开启测试重复推送的新手动运行准备一条新通知，保留被替换的上一条记录用于核对；各运行也各自保留状态产物。下一天生成新快照，午夜后不补发旧价格。

同日重跑可能几十秒就完成，准备日志会明确显示复用快照的采集时间、没有重新扫描 Steam；发送日志区分本次新提交、当天已接收而跳过、之前消息已验证送达。工作流成功表示步骤正常结束，并不等于本次发了新微信。下载产物在该次运行左侧“摘要”页底部的 Artifacts 区域，作业日志页不列出产物。

普通 Token 模式只确认 **PushPlus 已接收**，不证明微信已送达；自动日报及同一次运行的重试跳过已接收消息，开启测试重复推送的新手动运行可再发一条。明确拒绝或确认投递失败后，手动重跑可有限重试，最多 3 次。超时、发送结果不明时保留 uncertain 状态，停止自动重发，需要在 PushPlus 记录中核对。不能保证跨崩溃和 artifact 保存失败的严格恰好一次。

可选 PUSHPLUS_SECRET_KEY 能查询最终投递状态，但还需 PushPlus 开放接口及安全 IP 配置；GitHub 标准执行器出口 IP 会变化。所有 PushPlus 请求间隔至少 13 秒，发送 POST 不自动重试。失败只影响这一条通知，不再遍历几十页发送。

Actions 状态与报告均为 ZIP 下载产物，用于备份和查看；微信正文只有完整网页链接，**不是压缩包附件**。Pages 只发布 reports/site/ 的公开商品数据，不上传状态或凭证。

PushPlus 普通微信接口没有文档化的自定义通知跳转地址参数。当前实现发送可点击的 HTML 网页链接，可能先进入 PushPlus 消息页再点击链接；没有实现通知卡片一步直达外部网页。依据见 [官方接口文档](https://www.pushplus.plus/doc/guide/api.html)。

自动日报处理升级前的单条摘要记录时，若从未发送或已明确失败，准备/发送时可替换为页面链接，保留原快照、重试次数和过期时间；已接收、待确认、已送达、发送中或结果不明的记录保持原内容，避免当天补发。若今天已经收到摘要，自动日报的页面入口通知从次日开始；开启测试重复推送的新手动运行可立即准备并发送新版入口。

## Windows 本地预览

Python **3.12+**，生产代码只使用标准库。在项目目录运行：

    python -X utf8 -m unittest discover -s tests -v
    python -m steam_daily

默认生成 reports/site/index.html、offers.json、manifest.json 以及 reports/wechat-notification.html，不发微信、不部署、不保存投递状态。直接打开 index.html 可浏览和筛选，下载 JSON 需保留同目录的 offers.json。

已有数据可免扫描预览：

    python -m steam_daily --snapshot path/to/offers.json

实际部署后的发送分两步，日常由工作流自动完成：

    python -m steam_daily --prepare-send --restore --site-url https://example.github.io/repository/
    # 在这两步之间发布 reports/site 到该地址。
    python -m steam_daily --send --deployed-url https://example.github.io/repository/

本地准备一条新的测试通知时，在第一步追加唯一编号，例如：

    python -m steam_daily --prepare-send --restore --site-url https://example.github.io/repository/ --notification-id manual-test-1

发布网页后照常执行 --send。每次新测试换一个编号，同一编号用于失败后重试；GitHub手动工作流会自动提供运行编号。定时任务及关闭测试重复推送时不提供编号，沿用每日去重。

真实 Token 只通过环境变量或 GitHub Secrets 提供。--send 必须使用当天已准备的新版状态，并核对已发布网页；不能直接发送旧版分页状态。

config.json 可以调整阈值、分页上限、中文/英文和限速，固定国区。分页间隔默认 3 秒；总数变化/重复时最多完整重试一次，GET 临时失败有限重试并尊重限流；持续失败则停止，不发残缺名单。

## 费用与维护

公开仓库标准 GitHub Actions 执行器的运行分钟免费，GitHub Pages 可免费托管公开仓库静态网页；存储配额及 PushPlus 配额另算。报告留存 7 天、状态留存 30 天。没有自动提交、推送或自保活任务。

公开仓库连续 60 天没有活动时定时工作流可能停用，需要检查 Actions 状态。定时任务可能延迟或跳过。Steam 搜索内部接口及图片路径可能变化，不能保证每日成功。

实现与实测见 [DESIGN.md](DESIGN.md)、[VALIDATION.md](VALIDATION.md)。

## 官方资料

- [GitHub Pages 自定义工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [Pages 配置 Action](https://github.com/actions/configure-pages)、[发布 Action](https://github.com/actions/deploy-pages)
- [Actions 定时规则](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [Actions 费用](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [PushPlus 消息接口](https://www.pushplus.plus/doc/guide/api.html)、[开放接口](https://www.pushplus.plus/doc/guide/openApi.html)、[限制](https://www.pushplus.plus/doc/help/limit.html)
