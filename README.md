# tech-weekly · 每周科技周报自动推送（中文摘要）

从公开信源（RSS / arXiv / Hacker News / Hugging Face Daily Papers / PubMed / 微信公众号）抓取**五个领域**的最新进展，
去重、按领域配额排序，再用大模型把**标题和摘要改写成中文**，渲染成 HTML 邮件每周自动发到你的邮箱。

- **中文摘要**：DeepSeek 等大模型改写标题与摘要；原文英文标题保留在下方，方便回原文对照
- **大模型选稿**：按一套写死的编辑标准给候选条目打 0-10 分，低于阈值的直接丢掉，
  再合并"同一事件、不同标题"的重复——规则打分回答不了"值不值得看"，这一步能
- **五个领域**：AI / 大模型、机器人 / 具身智能、生物科技 / 医疗（含**脑机接口**子栏）、芯片 / 半导体、**中国科技动态**
- **纯 Python 标准库，零第三方依赖** —— 不需要 `pip install`，不会因为依赖升级而挂掉
- **跑在 GitHub Actions 上** —— 免费、不用开自己的电脑
- **信源可自由增删改** —— 全部写在 `config.json` 里
- **能在本地先预览** —— `preview` 模式生成 HTML，确认满意再让它真发信
- **有本地配置界面** —— `python ui.py` 打开网页，增删领域和信源、改收件人、跑预览、一键提交
- **省钱设计**：多条内容合并成少数几次请求；译文按内容落盘缓存，同一条不会重复付费

覆盖领域（可改）：**AI / 大模型**、**机器人 / 具身智能**、**生物科技 / 医疗**、**芯片 / 半导体**、**中国科技动态**

---

## 一、配置 GitHub Actions 自动发信

### 1. 拿到邮箱的 SMTP 授权码

注意：这里要的是**授权码**，不是登录邮箱的密码。

| 邮箱 | SMTP 服务器 | 端口 | 授权码在哪里申请 |
| --- | --- | --- | --- |
| QQ 邮箱 | `smtp.qq.com` | 465（SSL） | 设置 → 账号 → 开启「IMAP/SMTP 服务」→ 生成授权码 |
| 163 邮箱 | `smtp.163.com` | 465（SSL） | 设置 → POP3/SMTP/IMAP → 开启服务 → 新增授权密码 |
| Gmail | `smtp.gmail.com` | 465（SSL） | 需先开启两步验证，再生成「应用专用密码」 |
| Outlook | `smtp.office365.com` | 587（STARTTLS） | 账号安全 → 应用密码 |

### 2. API Key

默认用 DeepSeek：登录 [platform.deepseek.com](https://platform.deepseek.com/) →
API keys → 创建一个 → 复制那串 `sk-` 开头的 Key。

按默认配置（每期约 27 条、分 3 次请求），
**一期大约 2 分钱**；实测 27 条内容约 4600 token。

> 不配这个 Key，程序会保留英文原文照常发信（只是没有中文摘要）。
> 也可以随时用 `--no-llm` 只跑一次英文版。

### 3. 建仓库并把代码传上去

```bash
cd tech-weekly
git init
git add .
git commit -m "feat: weekly tech digest"
git branch -M main
git remote add origin https://github.com/<你的用户名>/tech-weekly.git
git push -u origin main
```

### 4. 配置 Secrets

仓库页面 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**，
逐个添加：

| Secret 名称 | 必填 | 说明 | 示例 |
| --- | --- | --- | --- |
| `SMTP_HOST` | ✅ | SMTP 服务器 | `smtp.qq.com` |
| `SMTP_PORT` | ✅ | 端口 | `465` |
| `SMTP_SSL` | ✅ | 465 填 `true`，587 填 `false` | `true` |
| `SMTP_STARTTLS` | | 只有 587 端口需要填 | `true` |
| `SMTP_USER` | ✅ | 你的邮箱账号 | `you@qq.com` |
| `SMTP_PASSWORD` | ✅ | 第 1 步的**授权码** | `abcdwxyzabcdwxyz` |
| `MAIL_FROM` | | 发件地址，留空用 `SMTP_USER` | `you@qq.com` |
| `MAIL_FROM_NAME` | | 发件人显示名 | `科技周报` |
| `MAIL_TO` | ✅ | 收件人，逗号分隔 | `a@x.com,b@x.com` |
| `MAIL_CC` | | 抄送 | `boss@x.com` |
| `TEST_RECIPIENT` | | 试发地址，填了之后 `run` 模式先发这里 | `you@qq.com` |
| `LLM_API_KEY` | ✅ | 第 2 步的 API Key | `sk-xxxxxxxx` |
| `LLM_MODEL` | | 换模型时填，默认 `deepseek-chat` | `deepseek-chat` |
| `LLM_BASE_URL` | | 换服务商时填 | `https://api.deepseek.com` |

### 5. 手动验证

仓库页面 → **Actions** → 左侧选 **Weekly Tech Digest** → 右侧 **Run workflow**：

| mode | 用途 |
| --- | --- |
| `llm` | **先跑这个**：只翻译 3 条固定样例，验证 Key 和中文质量，不抓信源、不发信 |
| `run` | 先试发给自己，成功后再发全体（推荐日常用） |
| `send` | 直接发给全体收件人 |
| `preview` | 只生成不发信，结果放在 artifact 里 |

跑完点进这次运行，可以下载 `weekly-digest-*` artifact 看到**这一期生成的 HTML 原件**，
即使发信失败也能查看内容。

### 6. 之后就是自动的

工作流默认**每周一 01:00 UTC（北京时间周一 09:00）**自动执行。
想换时间就改 `.github/workflows/weekly.yml` 里的 cron：

```yaml
on:
  schedule:
    - cron: "0 1 * * 1"   # 分 时 日 月 周 —— 周一是 1，这里是 UTC 时间
```

常用对照（北京时间 → 填进去的 UTC）：

| 北京时间 | cron |
| --- | --- |
| 周一 09:00 | `0 1 * * 1` |
| 周五 08:30 | `30 0 * * 5` |
| 周日 20:00 | `0 12 * * 0` |

---

## 二、本地运行

```bash
python main.py test                 # 体检全部信源，报告哪些挂了
python main.py llm                  # 测中文摘要：Key、模型名、中文质量（推荐先跑）
python main.py preview --days 7     # 生成 out\ 下的 HTML + TXT 预览（含中文摘要）
python main.py preview --no-llm     # 同样的预览，但不调用大模型、不花钱
python main.py send                 # 生成并发送
python main.py run                  # 先试发 TEST_RECIPIENT，成功后再发全体
```

Windows 用户也可以直接用附带脚本：

```powershell
.\run-weekly.ps1 -Mode llm        # 中文摘要自检
.\run-weekly.ps1 -Mode preview    # 只预览
.\run-weekly.ps1                  # 按 config.json 发送
```

凭据用环境变量传（**推荐**，避免密码进 git）：

```powershell
setx SMTP_USER "you@qq.com"
setx SMTP_PASSWORD "你的授权码"
setx MAIL_TO "reader@example.com"
setx LLM_API_KEY "sk-xxxxxxxx"
```

`setx` 之后要**新开一个终端**才生效。也可以直接填在 `config.json` 的 `smtp` / `llm` 段里，
但那样请务必确认这个文件没有推到公开仓库。

### 用图形界面改配置

不想手改 1400 行的 JSON，可以开一个本地网页：

```bash
python ui.py                  # 浏览器自动打开 http://127.0.0.1:8765
python ui.py --port 9000      # 换端口
python ui.py --no-browser     # 不自动开浏览器
```

想要更像"应用"而不是"网页"：

```bash
python app.py                     # 独立窗口：无地址栏、无标签页、独立任务栏图标
python app.py --install-shortcut  # 在桌面建快捷方式，以后双击就用（不弹黑框）
```

`app.py` 是同一套界面的外壳：用 Chromium 的 `--app=` 模式开窗口，并给它一个
单独的浏览器配置目录（`cache/app-window`），和你日常浏览的 Edge/Chrome 完全隔离。
没装 Edge/Chrome 时会退回默认浏览器打开标签页。

四个页签：

| 页签 | 能改什么 |
| --- | --- |
| 领域与信源 | 增删领域、增删信源、改类型/名称/权重/关键词；每个信源有「测试」按钮，直接看它返回几条 |
| 收件与投递 | SMTP 服务器、端口、加密方式、收件人、抄送；顶部显示各个密钥「已配置/未配置」 |
| AI 与选稿 | 模型、阈值、候选池、预算、抓取并发与健康度参数 |
| 运行与预览 | 体检 / 生成预览 / 发送（发送要勾确认框），日志实时滚动，右侧直接显示生成的 HTML |

三点设计说明：

- **密钥不会进 config.json**。界面里只显示状态，值仍旧从环境变量读；也正因为这样，
  改配置不需要碰密码。
- **保存前会校验**：URL、权重范围、必填字段、领域 id 格式、同领域内信源重名…
  有错会列在底部，不会写坏文件。
- **只监听 127.0.0.1**，所有写操作要带启动时随机生成的 token，
  防止你浏览器里别的页面顺手 POST 过来。

改完点「保存」只写本地文件；点「提交并推送」才会 commit 并 push。
推送需要 git 已经有可用凭据（一次性的设置：`git config --global credential.helper manager`，
然后任意一次 push 时在弹窗里登录一次）。

---

## 三、中文工作模式

```
抓取 63 个信源 → 去重排序 → 选出 27 条 → 每次 10 条合并成一次请求 → 中文标题 + 中文摘要
                                              ↓
                                    按内容哈希落盘缓存 cache/llm-cache.json
```

**节流与容错**，都是实测过的行为：

| 情况 | 程序行为 |
| --- | --- |
| 模型返回被 ` ```json ` 包住 / 前后带废话 / 中文引号 / 尾随逗号 | 自动修复并解析 |
| 模型漏返回了某几条 | 只对漏掉的那几条补一次请求 |
| 返回 HTTP 401 / 403（Key 错、没权限） | 立即失败，不做无意义重试 |
| 返回 HTTP 429 / 5xx 或网络错误 | 退避重试，最多 `max_retries` 次 |
| 整批翻译失败 | 这批保留英文原文，其余照常翻译，周报照发 |
| **所有条目都没翻成** | `send` 会中止发信（退出码 3），避免发一封"全英文的中文周报" |
| 没配 `LLM_API_KEY` | 提示一次，然后全体保留英文原文，不影响发信 |
| 同一条内容下期又出现 | 命中缓存，不再调用接口、不再付费 |

邮件里的排版是：**中文标题**（可点击）→ 灰色小字的英文原标题 → 中文摘要 → 来源与时间。
英文原标题一直保留，方便你拿它去搜索原文或核对细节。

### 换服务商 / 换模型

任何 OpenAI 兼容接口都能用，改 `config.json` 三行即可：

```jsonc
"llm": {
  "base_url": "https://api.deepseek.com",   // Kimi: https://api.moonshot.cn/v1
  "model": "deepseek-chat",                 // 通义: qwen-plus / OpenAI: gpt-4o-mini
  "api_key": ""                             // 留空则读环境变量 LLM_API_KEY
}
```

`price_in_per_m` / `price_out_per_m` 只用来在日志里估算花费，填错不影响功能。
如果你换的服务商不支持 `response_format: json_object` 这个参数，把 `translate.py` 里
`_chat()` 的 payload 中那一行删掉即可 —— 解析函数本身能容忍代码块和前后废话。

---

## 四、配置说明（`config.json`）

```jsonc
{
  "report": {
    "title": "科技周报",
    "window_days": 7,        // 抓最近几天
    "max_per_domain": 6,     // 每个领域最多几条（兜底值）
    "intro": "……",           // 邮件开头的说明
    "footer": "……"
  },
  "smtp": { /* 见上表；user/password/recipients 留空则从环境变量读 */ },
  "llm": {
    "enabled": true,         // false 则完全不调用大模型
    "base_url": "https://api.deepseek.com",
    "api_key": "",           // 留空则读 LLM_API_KEY
    "model": "deepseek-flash",
    "thinking": "disabled",  // DeepSeek V4 默认开思考模式，翻译/选稿都不需要
    "batch_size": 10,        // 每次请求合并多少条（越大越省，越大越容易漏条目）
    "use_cache": true,       // 译文落盘缓存
    "max_retries": 3,
    "price_in_per_m": 2.0, "price_out_per_m": 8.0, "currency": "CNY"
  },
  "fetch": {
    "timeout_seconds": 25, "workers": 10, "hn_min_points": 10,
    "retries": 2,                // 只对超时/5xx/429 重试，403·404 不重试
    "snapshot_fallback": true,   // 源挂了用上期快照补位
    "disable_after_failures": 4, // 连续失败 4 期后暂停抓取
    "cooldown_days": 21          // 暂停多久后自动重试
  },
  "domains": [
    {
      "id": "ai",
      "name": "AI / 大模型",
      "icon": "🤖",
      "enabled": true,
      "max_items": 6,                        // 这个领域每期几条
      "keywords": ["llm", "agent", "多模态"],  // 相关性打分 + 过滤用
      "sources": [
        { "type": "rss",  "name": "显示名", "url": "https://…/feed", "weight": 12 },
        { "type": "arxiv", "categories": ["cs.AI", "cs.LG"], "keywords": ["llm"], "weight": 13 },
        { "type": "hackernews", "queries": ["LLM"], "hn_keywords": ["llm", "agent"],
          "min_keyword_hits": 2, "weight": 9 },
        { "type": "huggingface_papers", "limit": 12, "weight": 16 },
        { "type": "pubmed", "name": "PubMed · 脑机接口", "weight": 14,
          "days": 30, "retmax": 12,
          "queries": ["\"brain-computer interface\"[Title/Abstract]"],
          "title_keywords": ["brain-computer", "bci", "eeg"],   // 标题必须含其一
          "min_title_hits": 1,
          "filter_keywords": ["neural interface", "eeg"],       // 标题+摘要再要求命中
          "min_filter_hits": 2,
          "group": "脑机接口" },
        { "type": "bing_news", "name": "Bing 新闻 · 脑机接口", "weight": 11,
          "group": "脑机接口",                     // 与 PubMed 同属一个子分组
          "queries": ["脑机接口", "植入式 脑机接口"],
          "title_keywords": ["脑机接口", "bci"], "min_title_hits": 1 }
      ]
    }
  ]
}
```

信源类型一共六种：

- **`rss`** —— 任意 RSS / Atom 地址，`url` 必填（微信公众号见下一节）
- **`arxiv`** —— 用 `categories` 指定 arXiv 分类（如 `cs.AI`、`cs.RO`、`q-bio.QM`、`cs.AR`、`eess.SP`）
- **`hackernews`** —— 通过 HN 官方 Algolia 接口按关键词搜高热度帖子
- **`huggingface_papers`** —— Hugging Face 每日论文榜
- **`pubmed`** —— PubMed 官方 E-utilities（免费、无需 Key），适合盯一个具体医学/生物主题
- **`bing_news`** —— Bing 新闻搜索（国内可直连、无需 Key），用关键词发现清单外的媒体
- **`web_search`** —— 通用网页搜索（需 Key，见下），可选 Brave / Tavily / SearXNG / Google CSE

### 搜索型信源（国内优先）

除了固定站点，还可以用**搜索**来发现内容。选 Bing 而不是 Google 的原因很实际：

| 方案 | 国内可直连 | 说明 |
| --- | --- | --- |
| **Bing 新闻 RSS** | ✅ | `www.bing.com/news/search?q=<关键词>&format=RSS`，实测稳定，每条自带真实媒体名和中文摘要 |
| Google News RSS | ❌ 需代理 | 内容更全（100 条），但本地跑要代理，所以不做默认 |
| 百度新闻 RSS | ❌ 已失效 | 旧接口现在返回"百度安全验证"页面 |

配置示例（`queries` 写中文即可，程序会自动 URL 编码）：

```jsonc
{ "type": "bing_news", "name": "Bing 新闻 · 脑机接口", "weight": 11,
  "group": "脑机接口",
  "queries": ["脑机接口", "脑机接口 临床", "植入式 脑机接口"],
  "title_keywords": ["脑机接口", "脑机", "bci", "神经接口"],
  "min_title_hits": 1 }
```

两个坑我已经在代码里处理了：

1. URL 里**只能有 `q` 和 `format=RSS`**。加上 `setmkt` / `setlang` / `mkt` 参数会返回 HTML 而不是 RSS。
2. Bing 对并发敏感，多个查询同时打过去会被限流（返回 HTML）。所以 Bing 请求是**串行 + 退避重试**的。

单个信源想临时停用，加 `"enabled": false` 即可，不用删配置（例如 Solidot 2026-10-22 起连接被拒，就先停用了）。

### 网页搜索（可选，需 Key）

> **Bing Web Search API 已经退役**（微软 2025-08-11 停止，[官方公告](https://learn.microsoft.com/zh-tw/lifecycle/announcements/bing-search-api-retirement)），
> 现在申请不到新 Key，所以本项目**不再支持 `provider: "bing"`**。
> 想用网页搜索，从下面选一个可用服务商。

`web_search` 信源支持四种服务商，按需选一个、填对应 Key 即可：

| provider | Key 来源 | 环境变量 | 国内直连 | 免费额度 |
| --- | --- | --- | --- | --- |
| `brave` | [Brave Search API](https://brave.com/search/api/) | `BRAVE_API_KEY` | 一般可以 | 有免费层 |
| `tavily` | [Tavily](https://tavily.com/) | `TAVILY_API_KEY` | 一般可以 | 每月免费额度 |
| `searxng` | 自建 [SearXNG](https://docs.searxng.org/) 实例 | `SEARXNG_URL` | 取决于实例 | 免费 |
| `google_cse` | Google Programmable Search | `GOOGLE_API_KEY` + `GOOGLE_CSE_ID` | ❌ 需代理 | 每天 100 次 |

配置示例（`config.json` 里已有一个 `enabled: false` 的占位，改好 Key 后把 `enabled` 改成 `true`）：

```jsonc
{ "type": "web_search", "name": "网页搜索", "provider": "brave",
  "api_key": "",                  // 留空则读 BRAVE_API_KEY
  "queries": ["中国 芯片 技术进展", "脑机接口 最新进展"],
  "limit": 8,
  "days": 7,
  "weight": 10,
  "enabled": true }
```

自托管 SearXNG 时，`provider` 填 `searxng`，`api_key` 填实例地址（如 `http://127.0.0.1:8080`），
需要该实例开启 `format=json`。

`weight` 是来源权重（越大越容易排到前面）。`keywords` 用来算相关性加分；
HN 和 arXiv 还会用它做**硬过滤**（HN 默认要求标题至少命中 2 个，避免把无关热帖带进来）。

HN 源还有两个专用参数：

```jsonc
{ "type": "hackernews", "queries": ["Huawei", "BYD"],
  "hn_keywords": ["huawei", "byd"], "min_keyword_hits": 1,
  "points_bypass": 1000000 }   // 关闭"高热度豁免"
```

默认情况下 HN 里 **≥150 分的帖子会跳过关键词检查**（免得漏掉真热点）。但定位很窄的领域
（例如“中国科技动态”）必须关掉它。把它设成一个不可能达到的分数即可关闭（实测：同一批 107 条候选里，带豁免会放进 37 条无关爆款；关掉后 14 条全部真正涉华）。

### 大模型选稿（`curation`）

规则打分看的是"来源靠不靠谱、新不新"，但看不出"这条到底重不重要"。打开这个开关后，
程序会把每个领域的前若干条送给大模型，按一条写死的编辑标准打分、过滤、合并同事件条目：

```jsonc
"curation": {
  "enabled": true,
  "score_threshold": 6.0,        // 0-10 的相关度阈值，低于它就丢掉
  "pool_per_domain": 12,         // 每个领域送审多少条（越多越贵）
  "min_keep_per_domain": 4,      // 每领域保底，防止整栏被清空
  "topic_dedup": true,           // 合并"同一事件、不同标题"
  "batch_size": 20,              // 一次请求送多少条
  "max_dedup_items": 40,
  "budget_cny": 0.1              // 本环节花费上限，超了自动退回规则分
}
```

评分标准写在 `curate.py` 的 `SCORE_SYSTEM` 里，可以直接改：九个档位、七个判断维度，
外加六条硬规则（热度≠重要性、不奖励标题党、纯融资≤4 分、综述教程≤4 分…）。

两点要留意：

- **关掉它**：`"enabled": false`，或运行时加 `--no-llm`，程序完全退回规则打分。
- **成本**：一期约 60 条送审，实测选稿环节 ¥0.029、中文摘要 ¥0.020，合计约 5 分钱。
  选稿比翻译便宜得多（只输出分数和一句理由），但换来的是"该不该上"的判断。

### 信源级精度控制（防止"顺带提一句"的内容混进来）

`pubmed` 这类按关键词检索的信源天生宽泛：你搜 `brain-computer interface`，PubMed 会把
摘要里提了一句 BCI 的阿尔茨海默论文也返回。三个配置项用来收紧：

```jsonc
"title_keywords": ["brain-computer", "bci", "eeg"],  // 标题必须命中，最可靠的信号
"min_title_hits": 1,
"filter_keywords": ["neural interface", "eeg", "..."], // 标题+摘要的命中数
"min_filter_hits": 2
```

**只按命中总数过滤会误杀**——真做这个主题的论文，
标题已经把主题说清楚了，摘要里不会反复重复术语，所以标题判断最有效。

同一个机制也用在话题很杂的综合媒体上，例如 `cnBeta`/`IT之家` 配一组科技关键词，
把娱乐、财经类内容挡在外面（实测 150 条里筛掉 83 条）。

### 领域内子分组（保证小众主题不被挤掉）

领域里的信源可以带 `group` 字段。有子分组时，领域名额会**按各组信源数量比例**分配，
小众主题就有保底位置，不会被大主题占满。例如生物医药 5 个名额、常规源 8 个 + 脑机接口 2 个：

```
常规组 4 条  +  ◆ 脑机接口 1 条
```

某组内容不够时，没用满的名额会被其他组的剩余内容按分数回收，不会浪费。

### 微信公众号怎么接进来的

微信**没有官方 RSS**。本项目的做法是接 [wechat2rss](https://wechat2rss.xlab.app) 的公开服务
（300+ 公众号，更新周期约 24 小时，每条形如
`https://wechat2rss.xlab.app/feed/<hash>.xml`，直接用 `rss` 类型即可）：

```jsonc
{ "type": "rss", "name": "量子位（公众号）", "weight": 14,
  "url": "https://wechat2rss.xlab.app/feed/7131b577c61365cb47e81000738c10d872685908.xml" }
```

已接入：量子位、机器之心、新智元、PaperWeekly、阿里技术。完整可用列表在
[wechat2rss 列表页](https://wechat2rss.xlab.app/list/all/)。

**局限说清楚**：公开服务只收录它自己在抓的公众号，你没法把任意公众号加进来。
想要某个号（例如某个脑机接口号），只能在
[wechat2rss 仓库](https://github.com/ttttmr/wechat2rss/issues) 推荐收录，作者通过后大家都能用；
或者自建部署（该项目提供付费私有部署）。

### 加一个自己的领域

在 `domains` 数组里照抄一段，改成自己的 `id` / `name` / `keywords` / `sources`。比如加"新能源"：

```jsonc
{
  "id": "energy", "name": "新能源 / 储能", "icon": "🔋", "enabled": true, "max_items": 5,
  "keywords": ["battery", "电池", "solar", "光伏", "fusion", "核聚变", "grid", "储能"],
  "sources": [
    { "type": "rss", "name": "CleanTechnica", "url": "https://cleantechnica.com/feed/", "weight": 10 },
    { "type": "hackernews", "queries": ["battery", "fusion"],
      "hn_keywords": ["battery", "solar", "fusion", "grid"], "min_keyword_hits": 2, "weight": 9 }
  ]
}
```

加完先跑 `python main.py test` 确认新信源能连上。

---

## 五、它内部是怎么做的

1. **并发抓取**：按 `workers` 起线程池同时拉全部信源；单个源超时/报错/改版只会被记进"异常信源"，
   不影响其他源和整封邮件。重试只针对"可能自己好起来"的错误（超时、连接断开、5xx、429），
   **403 / 404 这类确定性失败不重试**——白跑一次还拖慢整轮。单个源可以用 `user_agent` /
   `referer` / `timeout_seconds` / `retries` 覆盖全局设置。
2. **失败回落上期快照**：每个源上次抓到的内容都存在 `cache/source-state.json` 里。
   这周挂了就用上期内容补位，并在邮件底部注明"已用上期快照补 N 条"，
   而不是让栏目空着一块。连续 `disable_after_failures` 期失败会暂停抓取
   `cooldown_days` 天，冷却结束自动重试——源修好了能自己回来。如果确认源已经修复、
   不想等冷却期，跑 `python main.py reset --source "源名字"` 立刻解除暂停。
3. **时间窗过滤**：只保留 `window_days` 天内的条目。信源时间戳如果跑到未来（时区填错的常见症状），
   会被收敛到当前时间，避免虚假置顶。
4. **去重**：三层。
   ① 按正文链接归一化（去掉 `utm_*` 等追踪参数、去 `www.`、去尾部斜杠）；
   ② 按标题完全一致合并——专门处理"腾讯/搜狐/新浪分别转载同一篇稿子"；
   ③ 对**每节已选出的条目**再做近似去重，合并"同一事件、标题措辞不同"的重复。
   中文用字符 2-gram 余弦（阈值 0.39，不需要分词）；**英文必须用词集合 Jaccard（阈值 0.5）**，
   因为字符 n-gram 对英文几乎失效——任意两段英文都共享大量字母组合（th / he / in / er…），
   实测两篇毫不相关的长标题余弦也有 0.41，会稳定穿过 0.39 的阈值，把不同事件误合并。
   被合并的条目会作为"相关"链接挂在保留的那条下面。
5. **规则打分**：来源权重 × 时间新鲜度 + 关键词命中数 + 论文加分 + HN 热度加分；
   标题含"被捕/起诉/诉讼"等案件八卦词会**降权 14 分**，综述类论文小幅降权；
   融资/股价/估值等财经措辞也降权 4.5 分（生物医药领域的融资不降，因为那是行业信号），
   避免同一笔融资被多家媒体反复顶上首页。
6. **大模型选稿**（`curation`，可关）：规则分只能回答"新不新、来源靠不靠谱"，
   回答不了"值不值得看"。这一步把每领域前 `pool_per_domain` 条送给模型，
   按一条写死的编辑标准打 0-10 的相关度分（技术深度、新颖度、影响面、证据强度…），
   低于 `score_threshold` 的丢掉；再把"同一事件、不同标题"的条目合并。
   每领域有 `min_keep_per_domain` 保底，不会把某个栏目清空；花费有 `budget_cny` 上限，
   超了就退回规则分。**任何失败都自动降级**，绝不因为选稿挂了而发不出周报。
7. **信源级精度过滤**：见第四节。定位型信源（PubMed 脑机接口）和杂食型媒体（cnBeta）
   都要过一遍自己的关键词，避免"顺带提一句"的内容混进来。
8. **每领域配额 + 子分组保底 + 同源打散**：按 `max_items` 取前 N 条，取的时候轮转不同来源，
   避免整节被同一个源包场；有 `group` 的领域先按比例给子分组分名额（脑机接口就是靠这个
   在生物医药里保住一席）；某组没用满的名额会被其他组的剩余内容回收。
   窗口内条目不足时用稍旧条目补足并标注"稍早"。
9. **中文摘要**：见上一节。只翻入选的 20 多条，不翻全部候选，所以成本可控。
10. **渲染**：同一份内容渲染成纯文本和 HTML 两个部分（`multipart/alternative`），
    邮件客户端不支持 HTML 时自动降级；主题和发件人中文名按 RFC 2047 编码。
11. **发送**：SSL（465）或 STARTTLS（587）由 `smtp.ssl` / `smtp.starttls` 控制。
    一条都没抓到时不发空邮件；中文摘要全军覆没时也不发（见上一节的表格）。

---

## 六、常见问题

**信源大面积失败（日志里一片 ✗）**
多半是所在网络访问境外站点受限。本地跑的话给终端配代理：
`$env:HTTPS_PROXY="http://127.0.0.1:7890"`。GitHub Actions 上不存在这个问题。

**微信公众号源报错或内容不更新**
wechat2rss 是第三方公益服务，偶发抽风或改版。它挂了不影响其他 50 多个源，
周报照发（只会在文末"异常信源"里列出来）。第二天一般会自己恢复。

**某个领域一条都没有 / 全是英文源**
看日志里该领域的"窗口内候选"数量。候选少说明信源太少或过滤太严，
可以调松 `min_filter_hits`，或加几个信源。

**`llm` 报 `401 Authentication Fails`**
Key 写错、被删除，或余额不足。到服务商后台确认 Key 和余额。

**`llm` 报模型不存在（`400 Invalid model`）**
模型名不匹配。DeepSeek 用 `deepseek-chat`；Kimi 用 `kimi-k2-0905-preview` 之类；
通义用 `qwen-plus`。改 `config.json` 的 `llm.model` 或设 `LLM_MODEL`。

**某几条没有中文**
看日志里"第 x/y 批"的提示。程序会自动补一次，仍失败的那几条保留英文；
下一期因为缓存机制（失败不写缓存）会重新尝试翻译。

**想看这期花了多少钱**
日志里有一行 `token 输入 … / 输出 …，请求 N 次，缓存命中 M 条，预计花费 X CNY`。

**`535 Authentication failed`（发信）**
九成是把登录密码当授权码用了。QQ / 163 / Gmail 都必须用单独申请的授权码。

**`Connection unexpectedly closed` / 卡在"连接 …"**
端口和加密方式不匹配：465 要 `SMTP_SSL=true`，587 要 `SMTP_SSL=false` + `SMTP_STARTTLS=true`。

**邮件进了垃圾箱**
把发件人加进白名单。自建发信偶尔会被判为推广邮件，属正常现象。

**退出码 2 / 3 的含义**
`2` = 本期窗口内一条都没入选，不发空邮件；`3` = 配了 Key 但中文摘要全部失败，
不发全英文邮件。两种情况预览文件都已生成，先用 `llm` 或 `preview` 排查。

**Actions 到点没跑 / 跑晚了**
GitHub 的定时任务在高峰期会延迟几分钟到几十分钟，这是官方行为，不是配置错了。

---

## 七、文件结构

```
tech-weekly/
├─ main.py                      # 主程序：抓取 / 去重 / 排序 / 渲染 / 发信 / CLI
├─ translate.py                 # 中文摘要：批量请求、缓存、计费、容错解析
├─ curate.py                    # 大模型选稿：相关度打分、主题去重、预算闸门
├─ health.py                     # 信源健康度：失败计数、上期快照、冷却停抓
├─ ui.py                        # 本地配置界面（http.server，零依赖）
├─ ui.html                      # 界面本体（vanilla JS，单文件）
├─ app.py                       # 桌面启动器：独立窗口 + 桌面快捷方式
├─ config.json                  # 领域、信源、关键词、收件人、SMTP、LLM、选稿配置
├─ run-weekly.ps1               # Windows 本地一键运行
├─ docs/参考项目调研.md            # 同类开源项目对比与改造清单
├─ tests/test_smtp_flow.py      # 发信链路端到端测试（本地假 SMTP）
├─ tests/test_translate_flow.py # 中文摘要端到端测试（本地假大模型）
├─ tests/test_web_search.py     # 网页搜索信源测试（本地假搜索服务）
├─ tests/test_dedup.py          # 标题相似度与选稿解析回归测试（不联网）
├─ tests/test_health.py         # 信源健康度与快照回退测试（不联网）
├─ tests/test_fetch.py          # 抓取层：重试策略、按源覆盖、Bing 解析（不联网）
├─ .github/workflows/weekly.yml # GitHub Actions 定时任务
├─ cache/                       # 中文摘要缓存，已被 .gitignore 忽略
└─ out/                         # 生成的预览（HTML + TXT），已被 .gitignore 忽略
```

自检命令：

```bash
python main.py test                        # 信源体检
python main.py reset --source "Bing 新闻"   # 解除信源暂停（确认源修好之后用）
python main.py reset --all                  # 解除全部暂停
python main.py llm                         # 中文摘要自检（会真实调用一次接口，花费约 0.001 元）
python tests/test_smtp_flow.py             # 发信链路测试（本地回环，需联网抓内容）
python tests/test_translate_flow.py        # 中文摘要测试（本地假模型，不联网、不花钱）
python tests/test_web_search.py            # 网页搜索测试（本地假服务，不联网、不花钱）
python tests/test_dedup.py                 # 标题去重与选稿解析测试（不联网、不花钱）
python tests/test_health.py                # 信源健康度与快照回退测试（不联网、不花钱）
python tests/test_fetch.py                 # 抓取层测试（不联网、不花钱）
```

---

## 八、说明

- 本程序只读取各站点公开的 RSS / 开放 API（含 PubMed E-utilities），不抓取需要登录的内容，
  请遵守各信源的使用条款。
- **微信公众号是通过第三方公益服务 wechat2rss 转成 RSS 的**，不是微信官方接口。
  该服务只收录它自己在抓的公众号（300+ 个），你无法随意添加任意公众号；
  它的可用性由该服务决定，挂掉时只影响对应的几个源，不影响整封周报。
- 中文摘要由大模型生成，**可能与原文有偏差**；重要信息请点开原文核对，英文原标题一直保留在邮件里。
- `config.json` 里可以填密码和 Key（方便本地用），但请**不要**把填好的版本推到公开仓库。
