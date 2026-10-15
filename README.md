# Steam 每日优惠晨报（微信 / Bark）

每天北京时间 **09:30** 开始检查一次 Steam 国区优惠，向已配置的微信和 / 或 Bark 各发送 **一个完整优惠页面入口**。打开页面后查看封面卡片、限免和全部优惠。全部五折及以上商品放在带封面的 GitHub Pages 网页，支持搜索、筛选、排序和分页，不再拆成几十条消息。

09:30 是任务计划开始时间，采集、网页发布和 GitHub 调度会带来延迟，并非微信准点到账保证。没有每十分钟扫描，也没有单独的截止前任务。

## 每天北京时间 09:30 自动运行

1. 将本次修改提交并上传到仓库默认分支 **master**，确认 **.github/workflows/daily.yml** 已更新。定时任务只使用默认分支上的工作流。
2. 打开仓库 **Actions → Steam 优惠晨报**。如果显示 **Enable workflow**，点击启用；已启用时无需其他定时设置。
3. 已经成功发布的 **Settings → Pages → Source: GitHub Actions** 保持当前配置。微信沿用 **PUSHPLUS_TOKEN** 和可选 **PUSHPLUS_SECRET_KEY**；iPhone Bark 按下一节添加 **BARK_DEVICE_KEY**。至少配置一个推送渠道。
4. 等待下一个北京时间 **09:30** 自动运行，Windows 电脑无需常开。工作流中的 `30 1 * * *` 使用 UTC，对应北京时间 09:30。
5. 需要立即执行或失败后重试时，点击 **Run workflow** 并选择 **master**。手动入口执行正常日报流程，已经删除“仅生成预览”和“测试重复推送”选项；同日已接收的通知会去重，次日重新采集并发送。

工作流先部署 Pages，核验线上网页确为这份数据，再发送页面入口通知。网页地址可在 **Settings → Pages → Visit site** 查看，消息链接自动采用实际 Pages 地址，支持自定义域名。Pages 页面每天覆盖为最新快照，旧微信链接也打开最新名单，并提示日期变化。若 github-pages 环境限制部署分支，应允许 master。

**已有发送记录：** 删除临时测试入口后，旧测试状态中的发送次数和流水号继续用于每日去重，不会因清理功能而再次推送。旧分页状态中已有任何消息发送尝试时，停止旧版未发分页，当天仅更新网页，次日恢复每日一条。已经开始的旧任务仍使用旧代码，上传新版不会改变它。

定时任务可能延迟或在 GitHub 高负载时跳过；公开仓库连续 60 天无活动会自动停用定时工作流，需要重新启用。依据见 [GitHub 定时规则](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule) 和 [启用工作流](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows)。

## iPhone 配置 Bark

1. 在 iPhone 的 App Store 搜索并安装 **Bark**，打开 App 并允许通知。
2. 在 Bark 首页的示例地址中找到设备 Key。例如 `https://api.day.app/设备Key/推送内容`，只复制中间的 **设备Key**。
3. 打开 GitHub 仓库 **Settings → Secrets and variables → Actions → Secrets → New repository secret**，名称填 **BARK_DEVICE_KEY**，值填设备 Key。不要把完整示例地址填进去。
4. 默认使用 Bark 公共服务器 `https://api.day.app`。使用自建服务时，在同一页面的 **Variables** 标签添加 **BARK_SERVER_URL**，填 HTTPS 服务基础地址（默认 HTTPS 端口，可带部署路径），不要包含设备 Key、查询参数或 `/push`。
5. 将新版代码上传到默认分支 **master** 后，在 **Actions → Steam 优惠晨报 → Run workflow** 执行正常日报；也可以等待次日自动运行。通知标题带日报日期，点击通知打开完整优惠网页。

配置了两个渠道就分别通知微信和 Bark；只配置 BARK_DEVICE_KEY 时只发 Bark。未配置 Bark 密钥时跳过 Bark。同一天先收到微信、之后才启用 Bark，也可以收到当天的第一条 Bark 通知，不会重复发送微信。

Bark 使用普通通知级别，通知分组为“Steam 优惠晨报”，保存到 App 历史记录；声音和显示效果受 iPhone 通知与专注模式设置影响。点击跳转使用官方 `url` 参数。依据：[Bark 使用教程](https://github.com/Finb/Bark/blob/master/docs/tutorial.md)、[请求参数](https://github.com/Finb/Bark/blob/master/docs/params.md)。

Key 仅通过 GitHub Secret 或本地环境变量提供，发送时放在 JSON 请求体中；公开网页、状态产物和日志不保存设备 Key。

## 网页与精选规则

- 单次顺序遍历公开国区优惠搜索分页，按实际获取并去重后的商品筛选折扣 ≥50%；包含游戏、DLC、软件、礼包、捆绑包等，不只截取热门商品。采集期间总数变化时继续，重复商品采用后一次读到的数据，不重新扫描。目录移动可能遗漏个别商品，不保证同一时刻的完整快照；检测到变化或重复时在网页提示。网页每页展示 24 项，全部采集结果仍在页面数据和 offers.json 中。
- 封面直接读取 Steam 搜索结果中的实际 HTTPS 图片地址，不猜测 CDN 路径，不为封面逐个抓取数千个详情页。图片缺失或加载失败时显示占位图。搜索小封面可能清晰度有限。
- 折扣精选最多 5 项：优先好评率 ≥80%、评测量 ≥500 的商品，按评测量降序，再以折扣降序、价格升序补足。评测不足不编造评分；精选可能包含 DLC 或软件，不代表个性化推荐。评测来自同一搜索结果，不增加额外全站请求。
- 100% 折扣且价格为零的候选需在详情页核验限时永久领取；排除免费周末、长期免费、Demo 和无国区公开售价的条目。
- 可靠领取截止时间换算北京时间，并标出未来 24 小时内结束的限免。无时区的原文保留、注明未核实时区。公告发布时间不当作活动开始时间，多数普通折扣的起止时间仍未公开。
- 微信正文只有完整网页入口，全部限免和折扣商品在网页展示；商品数量与名称长度不增加微信消息数量。
- 自动日报每天只检查一次，短活动可能在两次检查之间错过。价格、领取条件及需要的本体以 Steam 商品页为准；不自动登录、领取或购买。

## 发布、去重与失败

工作流顺序：测试 → 读取 Pages 地址 → 采集或恢复当天快照 → 生成网页及页面入口通知 → 上传 Pages 文件 → 部署 → 核验公开 manifest.json 的数据哈希 → 向已配置的微信 / Bark 发送。发布失败、地址不匹配或线上数据未更新时不发消息。Pages CDN 尚未同步时停止，稍后手动重跑即可。

状态 artifact 保存 state.json 和与它绑定的 offers.json。同一天复用原始数据，不重新采集；若原数据被改动或缺失则停止。定时运行、手动运行和失败后重试均沿用当天发送记录；已有测试状态中的额外元数据保持兼容，发送流水不会重置。下一天重新采集并生成新快照，午夜后不补发旧价格。

同日重跑可能几十秒就完成，准备日志会明确显示复用快照的采集时间、没有重新扫描 Steam；发送日志区分本次新提交、当天已接收而跳过、之前消息已验证送达。工作流成功表示步骤正常结束，并不等于本次发了新微信。下载产物在该次运行左侧“摘要”页底部的 Artifacts 区域，作业日志页不列出产物。

普通 Token 模式只确认 **PushPlus 已接收**，不证明微信已送达；定时运行和手动重跑均跳过当天已接收消息。明确拒绝或确认投递失败后，手动重跑可有限重试，最多 3 次。超时、发送结果不明时保留 uncertain 状态，停止自动重发，需要在 PushPlus 记录中核对。不能保证跨崩溃和 artifact 保存失败的严格恰好一次。

可选 PUSHPLUS_SECRET_KEY 能查询最终投递状态，但还需 PushPlus 开放接口及安全 IP 配置；GitHub 标准执行器出口 IP 会变化。所有 PushPlus 请求间隔至少 13 秒，发送 POST 不自动重试。失败只影响这一条通知，不再遍历几十页发送。

Bark 使用独立的 `bark.status` 和 `bark.attempts` 发送记录，与微信记录一起保存和恢复；当天已接受时跳过重发，次日重新创建。一个渠道投递失败仍尝试另一个渠道，最后汇总失败；状态保存失败会立即停止。旧微信分页的当天抑制仅作用于微信。

Bark 返回成功只表示服务端接受请求，不能证明 iPhone 已显示通知。超时、HTTP 错误、无效响应或不明确的服务端失败记为 `uncertain`，不自动重发，需要先核对手机历史记录及服务端日志。明确 JSON 参数或鉴权拒绝时记为 `failed`，后续手动重跑最多尝试 3 次。发送 POST 不自动重试，午夜后不补发旧日报；状态 artifact 丢失或保存失败时仍不能保证严格恰好一次。

Actions 状态与报告均为 ZIP 下载产物，用于备份和查看；微信正文只有完整网页链接，**不是压缩包附件**。Pages 只发布 reports/site/ 的公开商品数据，不上传状态或凭证。

PushPlus 普通微信接口没有文档化的自定义通知跳转地址参数。当前实现发送可点击的 HTML 网页链接，可能先进入 PushPlus 消息页再点击链接；没有实现通知卡片一步直达外部网页。依据见 [官方接口文档](https://www.pushplus.plus/doc/guide/api.html)。

自动日报处理升级前的单条摘要记录时，若从未发送或已明确失败，准备/发送时可替换为页面链接，保留原快照、重试次数和过期时间；已接收、待确认、已送达、发送中或结果不明的记录保持原内容，避免当天补发。若今天已经收到摘要，页面入口通知从次日开始。

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

真实 Token 只通过环境变量或 GitHub Secrets 提供。--send 必须使用当天已准备的新版状态，并核对已发布网页；不能直接发送旧版分页状态。

config.json 可以调整阈值、分页上限、中文/英文和限速，固定国区。分页间隔默认 3 秒，每次采集只遍历一轮，按各页当前总数判断结束；总数减少导致末尾为空时正常完成。错误响应、错误偏移、尚未到末尾却返回空页或超过分页上限时仍停止。单个 GET 请求临时失败仍有限重试并尊重限流，不从第一页重新扫描。

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
