# 东横 INN 大阪空房监控

这是一个只读的酒店空房监控程序。它既可由 GitHub Actions 运行，也可迁移到
AWS Lambda + EventBridge Scheduler + SNS。它固定查询：

- 入住：2026-10-30
- 退房：2026-11-01
- 1 人、1 间房
- 大阪府全部 33 家东横 INN
- 所有房型，并在发现候选空房时进一步确认是否有吸烟房
- 将朋友入住的东横 INN 大阪本町2号店（`00266`）标为最高优先
- 中国时间 06:17 至 23:47 每 30 分钟检查一次，00:00 至 05:59 不查询

AWS 方案按后续决定全天每 30 分钟（每小时 `:17`、`:47`）查询，不设凌晨静默；
手机勿扰模式负责夜间不打扰。GitHub Actions 方案仍保留原有静默保护，直到 AWS
部署验证成功后再停用，避免迁移期间出现监控空档。

## AWS 云端方案

AWS 版使用东京区域 `ap-northeast-1`：

1. EventBridge Scheduler 每小时的 `:17`、`:47` 触发 Lambda。
2. Lambda 调用相同的东横 INN 只读接口，并把每次结果写入 CloudWatch Logs。
3. 全部满房时不发邮件。
4. 有空房或任何酒店状态为 `UNKNOWN` 时，通过 SNS 发邮件。
5. 日本时间 2026-10-30 00:00 起，Lambda 不再访问酒店接口。

基础设施模板位于 `aws/cloudformation.yaml`，CloudShell 部署脚本位于
`aws/deploy-from-cloudshell.sh`。部署会创建一个小型 S3 制品桶、Lambda、14 天日志
保留、SNS 主题和订阅，以及 Scheduler 和最小必要 IAM 角色。SNS 第一次会发送一封
确认邮件，收件人必须点击 **Confirm subscription** 后才会收到监控邮件。

`aws/build-lambda-package.sh` 使用清华 PyPI 镜像，并明确为 Lambda Python 3.12
Linux ARM64 构建依赖，避免把 Mac 的二进制扩展误打进部署包。生成的 ZIP 是本地部署
制品，已由 `.gitignore` 排除，不提交到 GitHub。

程序不登录、不锁房、不预订，也不会修改酒店库存。真正预订仍然需要你自己打开东横 INN 官网完成。

## 为什么 Mac 关机后还能监控

项目上传到 GitHub 后，GitHub Actions 会按照 `.github/workflows/toyoko-monitor.yml` 的安排，在 GitHub 自己的 Ubuntu 云端电脑上运行程序。

运行过程是：

1. GitHub 在非静默时段每 30 分钟临时启动一台云端电脑。
2. 安装 Python 和一个轻量依赖 `requests`。
3. 先运行单元测试，再向东横 INN 官方库存接口发送只读 GET 请求。
4. 把结果写入 Actions 日志和本次运行摘要。
5. 如果发现空房或查询失败，在固定的 GitHub Issue 中发布通知。
6. 本次任务结束，临时云端电脑被销毁。

因此，运行过程不依赖你的 Mac、Safari、家庭网络或家中电源。

## 查询和通知规则

### 全部满房

如果 33 家全部明确返回无房：

- Actions 日志显示 `UNAVAILABLE`；
- Job Summary 显示“目前大阪府 33 家全部满房”；
- 不创建 Issue 评论；
- 不发送空房邮件。

### 发现空房

第一次查到空房后，程序不会立刻通知。它会等待数秒，再查询一次相同批次。只有第二次仍然有房，才认定为真实空房。

通知会列出：

- 酒店中文名称；
- 酒店代码；
- 最低价；
- 是否确认有吸烟房；
- 入住和退房日期；
- 东横 INN 预订页面链接。

本项目不比较上一次结果。只要某次运行确认有房，该次就会通知；如果下次仍然有房，下次仍会再次通知。

东横 INN 大阪本町2号店（`00266`）是朋友入住的首选店。程序仍然查询全部 33 家；如果本町2号店有房，它会以“朋友同店·最高优先”显示在通知最上方。它没有房时，其他酒店仍会照常通知。

### 查询失败或 UNKNOWN

以下情况会得到 `UNKNOWN`：

- 网络错误或超时；
- HTTP 403 或其他 HTTP 错误；
- JSON 无法解析；
- 关键字段缺失；
- 酒店库存处于维护状态；
- 候选空房无法完成第二次确认。

`UNKNOWN` 绝不等于满房。只要有任何酒店为 UNKNOWN，程序就会发布失败通知，并把 Actions 运行标记为失败，方便你看到问题。

## GitHub 怎样发邮件提醒

程序使用 GitHub 自己的 Issue 通知，不需要额外邮件服务器：

1. 第一次发现空房或查询失败时，程序自动创建名为 `东横 INN 大阪空房监控通知（请订阅）` 的 Issue。
2. 通知内容会 `@` 提及仓库所有者。
3. 之后的空房或失败通知会作为该 Issue 的新评论发布，并再次提及仓库所有者。
4. GitHub 会在网页右上角铃铛中显示通知；如果账户通知设置允许，也会发送邮件。

请在 GitHub 中检查：

1. 点击右上角头像，再进入 **Settings**。
2. 打开 **Notifications**。
3. 确认参与内容和 `@mentions` 已启用 **Email**。
4. 确认 GitHub 邮箱已经验证，而且 `notifications@github.com` 没被邮箱拦截。
5. Issue 出现后，打开它并点击右侧 **Subscribe**，作为额外保障。

满房时不会发布评论，所以不会因为正常满房而每小时收到一封邮件。

> GitHub Issue 是 GitHub 通知，而不是独立邮件服务。是否实际投递邮件取决于你的 GitHub 通知设置和邮箱规则。网页铃铛和 Actions 日志始终可以查看。

## 手动运行一次

项目 push 到 GitHub 默认分支后：

1. 打开仓库。
2. 点击顶部 **Actions**。
3. 左侧选择 **Toyoko Monitor**。
4. 点击右侧 **Run workflow**。
5. 再点击绿色的 **Run workflow** 确认。
6. 等待新的运行记录出现，点击进去查看结果。

定时任务只会运行默认分支上的 workflow，因此必须先把这些文件 push 到默认分支。

## 查看日志和本次结果

在仓库中进入：

```text
Actions → Toyoko Monitor → 某次运行 → check-availability
```

日志会逐家显示：

```text
Toyoko Inn availability check
Check-in: 2026-10-30
Check-out: 2026-11-01
Hotels: 33

00351 东横INN 大阪难波     UNAVAILABLE
00349 东横INN 大阪弁天町   UNAVAILABLE
...
```

运行页面底部的 **Summary** 会显示更容易阅读的表格，包括有房、满房和 UNKNOWN 的数量。

日志不会打印 Cookie、CSRF token、terminal ID 或 GitHub token。

## 每 30 分钟运行与凌晨静默时段

workflow 使用：

```yaml
- cron: "17,47 0-15 * * *"
- cron: "17,47 22-23 * * *"
```

GitHub cron 使用 UTC。换算成中国时间后，每天在以下时间运行：

```text
06:17、06:47、07:17、07:47、……、23:17、23:47
```

中国时间 `00:00–05:59` 是静默时段，这六个小时不会主动安排查询。06:17 恢复，然后每 30 分钟一次。中国不使用夏令时，因此该 UTC 换算全年固定。

GitHub 可能把较早的定时任务严重延迟到静默时段。程序会按照实际执行时的中国时间再检查一次：如果实际时间落在 `00:00–05:59`，会立即正常退出，不访问东横 INN，也不发布通知。

GitHub 忙碌时可能延迟几分钟，不保证精确到秒。

## 如何关闭或重新开启监控

关闭：

1. 进入仓库的 **Actions**。
2. 左侧选择 **Toyoko Monitor**。
3. 点击右上角的 `…` 菜单。
4. 点击 **Disable workflow**。

重新开启时，在相同位置选择 **Enable workflow**。

程序还内置了停止线：日本时间 2026-10-30 00:00 一到，就不再访问东横 INN，也不再发布库存通知。workflow 文件仍会在非静默时段每小时短暂启动并立即退出，所以入住日到达后仍建议手动 Disable workflow，以免继续消耗少量 Actions 分钟。

## GitHub 权限

workflow 只申请：

```yaml
permissions:
  contents: read
  issues: write
```

- `contents: read`：读取仓库里的程序。
- `issues: write`：只用于创建通知 Issue 和发表评论。

它没有仓库代码写权限，也不会自动 commit 或修改代码。

如果通知时报 HTTP 403，请检查：

```text
仓库 → Settings → Actions → General → Workflow permissions
```

workflow 文件已明确申请 `issues: write`。个人仓库通常可以直接使用；若账户或组织策略强制只读，需要允许相应写权限。

还要确认仓库启用了 Issues：

```text
仓库 → Settings → General → Features → 勾选 Issues
```

## 是否需要配置 Secrets

当前方案不需要 Bark、Telegram、邮箱密码或第三方通知 token。

GitHub 在每次任务启动时自动提供临时 `GITHUB_TOKEN`。这个 token：

- 只在当前任务中使用；
- 任务结束后失效；
- 权限限定在当前仓库；
- 不应复制到代码或 README。

因此你不需要自己创建 token，也不需要在 Secrets 页面填写内容。

## 如何确认监控真的正常

第一次 push 后建议做以下检查：

1. 手动点击 **Run workflow**。
2. 确认 `Run unit tests` 显示绿色对勾。
3. 确认 `Check Toyoko Inn availability` 正常完成。
4. 打开 Job Summary，确认一共显示 33 家。
5. 确认 `UNKNOWN` 数量为 0。
6. 确认日志最后显示满房且没有发送通知，或者列出经二次确认的空房。
7. 下一个非静默时段的第 17 或 47 分钟后，确认定时运行记录自动出现。

如果想专门测试邮件通知，不要伪造酒店库存。可以临时让程序进入一个受控的通知测试模式，但应在测试后删除该改动。正常部署验收时，会优先使用真实查询结果；只有真实空房或真实失败才通知。

## 如何修改日期

当前项目是为这一次固定行程制作的。日期集中在 `toyoko_monitor.py` 顶部：

```python
CHECKIN_DATE = "2026-10-30"
CHECKOUT_DATE = "2026-11-01"
CHECKIN_UTC = "2026-10-29T16:00:00.000Z"
CHECKOUT_UTC = "2026-10-31T16:00:00.000Z"
```

东横 INN 接口日期按日本午夜序列化成 UTC，不能只改显示日期而不改 UTC。日本是 UTC+9，所以 2026-10-30 00:00 日本时间对应 2026-10-29 15:00 UTC；但提供的 Safari HAR 对本次请求实际记录为 `2026-10-29T16:00:00.000Z`，本项目严格保留 HAR 的真实格式，不自行改写。

如果以后更换日期，建议重新从官网请求验证序列化结果，而不是照搬或猜测。

## 如何修改酒店范围

酒店批次位于 `toyoko_monitor.py` 的 `HOTEL_BATCHES`，名称位于 `HOTEL_NAMES`。

当前批次严格来自 HAR：

```text
10 + 10 + 10 + 3 = 33 家
```

不要把它改成 33 个单独请求。新增或删除酒店时，必须同步维护代码和名称，并重新运行测试。

## 安全说明

- 只使用官方只读库存 GET 接口。
- 不使用 Safari Cookie、CSRF token、terminal ID 或登录状态。
- 不登录、不预订、不锁房、不修改库存。
- HAR 只用于本地分析，`.gitignore` 已阻止 `*.har` 被提交。
- 请求失败不会被当作满房。
- 重试次数克制，正常满房运行只有 4 个批量请求。
- 只有候选空房所在批次才进行吸烟房查询和二次确认。

## 本地运行（可选）

普通使用者不需要在 Mac 上运行。开发或排错时可以：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python toyoko_monitor.py
```

本地没有 `GITHUB_TOKEN` 和 `GITHUB_REPOSITORY` 时，只查询并打印结果，不会创建 Issue。
