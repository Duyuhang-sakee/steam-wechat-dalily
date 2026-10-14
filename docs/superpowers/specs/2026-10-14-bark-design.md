# Bark 日报推送

任务分类：Small Feature。哥大选择增加 Bark，用 iPhone 接收日报。

## 行为与边界

每天北京时间 09:30 的现有任务继续采集、部署 GitHub Pages、核验公开快照后发送。配置了 PUSHPLUS_TOKEN 就发送微信，配置了 BARK_DEVICE_KEY 就发送 Bark，两者可以同时启用。没有 Bark 密钥时保持现有行为。

Bark 通知使用简短中文正文、日报日期标题、Steam 优惠晨报分组和普通 active 提醒，点击跳到带日期参数的完整优惠网页。使用 JSON POST /push，device_key 只放在请求体。默认服务器为 https://api.day.app，可用 BARK_SERVER_URL 指定 HTTPS 服务地址。

## 状态与失败

保留现有 schema、mode、微信 pages 和所有旧流水。可选 bark 字段只保存 status 和 attempts，在首次实际发送前加入当天状态。微信和 Bark 分别去重；旧微信已经接收或旧分页被抑制时，仍可首次发送 Bark。次日重新创建状态。

发送前保存 sending；服务端明确接受后保存 accepted。超时、HTTP 错误、无效响应或不明确的服务端失败保存 uncertain，重跑不自动补发；明确 JSON 参数拒绝保存 failed，最多尝试三次。Bark 没有本项目可用的最终送达查询，accepted 不代表 iPhone 已显示通知。

渠道请求顺序执行，一方投递失败仍尝试另一方，最后汇总错误。状态保存失败立即停止。保留 Actions concurrency 和失败后 artifact 保存。跨运行状态 artifact 丢失或保存失败仍无法保证严格恰好一次。

## 验证与配置

通过本地 HTTP 服务器测试真实 JSON 请求、同日去重、部分失败、超时和 HTTP 错误不重试、版本核验、过期检查、旧状态兼容和 artifact 恢复。运行现有全部回归和 Python 编译检查，解析并核对工作流。

Actions 只在最后发送步骤传入 Bark 密钥。README 提供 App 安装、授权通知、复制 device key、GitHub Secret 和可选服务器变量的配置步骤。源码、日志、公开网页和状态中不写入真实密钥。不提交或推送代码。

官方协议依据：[发送方式](https://github.com/Finb/Bark/blob/master/docs/tutorial.md)、[参数](https://github.com/Finb/Bark/blob/master/docs/params.md)、[服务端处理](https://github.com/Finb/bark-server/blob/master/route_push.go)。
