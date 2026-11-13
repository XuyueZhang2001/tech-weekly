# tech-weekly · 每周一封中文科技周报

它每周一早上，把七个领域里**真正值得看**的科技进展筛出来，用中文写好摘要，发成一封邮件到你邮箱。
不用开电脑，不用每天刷十几个网站。

> 邮件长这样（节选）：
>
> **删除 LLM 后训练中的权重竟有奇效**
> <sub>The Surprising Effectiveness of Deleting Weights in LLM Post-Training</sub>
> 研究发现，仅将预训练大模型不到 0.05% 的权重置零、不做其他修改，在三种后训练范式下即可匹配完整微调的效果。
> Hugging Face Daily Papers · 📄 论文 · 11-07 · [原文](https://huggingface.co/papers/2611.10866)

---

## 目录

- [一、它会给你什么](#一它会给你什么)
- [二、它是怎么跑起来的](#二它是怎么跑起来的)
- [三、开始使用（约 10 分钟）](#三开始使用约-10-分钟)
- [四、用起来之后](#四用起来之后)
- [五、它替你兜住了哪些坑](#五它替你兜住了哪些坑)
- [六、花多少钱](#六花多少钱)
- [七、常见问题](#七常见问题)
- [八、进阶](#八进阶)
- [九、文件结构](#九文件结构)
- [十、说明与边界](#十说明与边界)

---

## 一、它会给你什么

**七个领域，每期约 33 条**（可改）：

| 领域 | 每期条数 | 主要信源 |
| --- | --- | --- |
| 🤖 AI / 大模型 | 6 | MIT Tech Review、Hacker News、arXiv、Hugging Face 论文榜 |
| 🦾 机器人 / 具身智能 | 4 | IEEE Spectrum、The Robot Report、TechCrunch + **国内具身公司**（宇树、智元、优必选、星动纪元、傅利叶） |
| 🧬 生物科技 / 医疗 | 4 | STAT News、Nature Biotech、PubMed + **国内脑机公司**（脑虎、博睿康、强脑、阶梯医疗），含**脑机接口**子栏 |
| 🔌 芯片 / 半导体 | 5 | EE Times、IEEE Spectrum、Tom's Hardware |
| 🇨🇳 中国科技动态 | 4 | 36氪、IT之家、cnBeta、量子位、机器之心、SCMP |
| 🏢 公司动态 / 官方发布 | 5 | **各公司官方 newsroom**（OpenAI、NVIDIA、Google、DeepMind、Meta、Apple、微软、AWS、三星）+ **中国企业**（华为、DeepSeek、Kimi、字节、阿里、腾讯、智谱、商汤，经 Bing 站内查询） |
| 🏛️ 政策与监管（中美） | 5 | **Bing 新闻 site:gov.cn**（科技部/工信部/发改委/国务院）、**科技日报 / 中国科技网**、信通院、白宫总统行动、Federal Register、CSIS、ITIF、Politico |

两个新增栏目是刻意做成"一手来源优先"的：

- **公司动态**：直接订阅各家官方新闻室，而不是等媒体转述。产品发布、组织调整（合并/裁员/重组/高管变动）
  这类消息，官方渠道通常比媒体快半步且不会失真。
- **政策与监管**：部委和省市的 `gov.cn` 站点大多没有 RSS，所以用 Bing 的 `site:gov.cn` 查询抓一手政策；
  美国侧用白宫总统行动、Federal Register（联邦规则公告）和智库。

**中国企业为什么走 Bing 而不是 RSS**：实测华为、DeepSeek、Kimi（月之暗面）、智谱、通义等
官网都没有可用 RSS（HTML 页面或 503），第三方公众号桥接 wechat2rss 的 395 个号里也没有这些官方号
（以安全圈和科技媒体为主）。所以用 `site:huawei.com 发布`、`DeepSeek 官方 发布`、
`Kimi 月之暗面 发布` 这类站内查询来接近官方口径，效果比等媒体转述好。

**科技日报**同理：`stdaily.com` 没有 RSS，改用 `site:stdaily.com 人工智能` 和
`科技日报 人工智能 政策` 查询。实测能拿到省市"人工智能+"行动方案、部委政策解读这类宣传口内容。

每个领域还可以写一条 `curation_hint`，告诉大模型这一栏该看重什么。政策栏就是靠它
把"地方试点也算高价值"讲清楚，否则模型会按影响力把地方政策压到阈值以下。

每一条都包含：

- **中文标题** + **中文摘要**（大模型改写，不是机翻）
- 灰色小字的**英文原标题**，方便你搜索原文、核对细节
- 来源、类型、发布时间，以及**可点击的原文链接**
- 同一事件被多家媒体报道时，只留一条，其余作为「相关」链接挂在下面

邮件同时生成 HTML 和纯文本两个版本，客户端不支持 HTML 也能读。

**几个你可能在意的点**：

- **零第三方依赖**：只用 Python 标准库，不需要 `pip install`，不会因为依赖升级而坏掉
- **跑在 GitHub Actions 上**：免费，不用开自己的电脑，也不用服务器
- **能先看再发**：`preview` 模式生成 HTML，你确认满意再让它真发

---

## 二、它是怎么跑起来的

```
每周一 09:00（北京时间）
        ↓
抓取 88 个信源  →  去重  →  大模型打分筛选  →  改写成中文  →  渲染 HTML  →  发邮件
        ↓
任何一步出问题都降级，不让整封周报发不出去
```

全部逻辑在 `main.py`，跑在 GitHub 的免费 runner 上。你不需要服务器，也不需要一直开着电脑。

---

## 三、开始使用（约 10 分钟）

### 你要准备三样东西

**1. 一个 GitHub 账号**（免费）——用来托管代码和跑定时任务。

**2. 一个邮箱的 SMTP 授权码**——注意是**授权码**，不是你登录邮箱的密码。

| 邮箱 | SMTP 服务器 | 端口 | 授权码在哪申请 |
| --- | --- | --- | --- |
| QQ 邮箱 | `smtp.qq.com` | 465 | 设置 → 账号 → 开启「IMAP/SMTP 服务」→ 生成授权码 |
| 163 邮箱 | `smtp.163.com` | 465 | 设置 → POP3/SMTP/IMAP → 开启服务 → 新增授权密码 |
| Gmail | `smtp.gmail.com` | 465 | 先开两步验证，再生成「应用专用密码」 |
| Outlook | `smtp.office365.com` | 587 | 账号安全 → 应用密码 |

**3. 一个大模型 API Key**（可选，但强烈建议）

默认用 DeepSeek：[platform.deepseek.com](https://platform.deepseek.com/) → 注册 → 充几块钱 →
API keys → 创建 → 复制 `sk-` 开头那串。**一期花费约 2 分钱**，充 10 块能用一年多。

> 不配 Key 也能用：程序会保留英文原文照常发信，只是没有中文摘要。

---

### 先分清：哪些东西在 GitHub，哪些在你电脑上

这套东西分两半。**搞不清这一点，就会以为"我配了啊怎么没生效"**——因为你的配置和线上跑的东西，是两份。

```
你的电脑（本地）                        GitHub（线上）
─────────────────────────              ──────────────────────────────
代码副本                               仓库：代码 + config.json
python ui.py → 本地界面                 GitHub Actions 每周自动跑
  ├─ config.json          ──推送──>    ↳ 读 Secrets 才能发信
  └─ config.local.json    ──写入──>    Secrets（加密保存）
     （你的授权码/Key）                  （同样的授权码/Key）
```

| 东西 | 存在哪 | 谁需要它 |
| --- | --- | --- |
| 代码、信源列表、关键词、条数 | 仓库里的 `config.json` | 自动任务 + 你本机 |
| 邮箱授权码、API Key | ① 你电脑上的 `config.local.json`<br>② 仓库的 **Secrets** | ① 本地手动跑<br>② 每周自动跑 |

所以要配两处：**先在本地界面填一次**（方便预览、调试），
**再把同样的值同步到 GitHub Secrets**（让自动任务能发信）。第 3、4 步分别做这两件事。

> **「界面」到底是什么？**
> 就是你在自己电脑上执行 `python ui.py` 之后，浏览器打开的那个 `http://127.0.0.1:8765`。
> 它是**跑在你电脑上的一个本地网页**——不是网站，不在 GitHub 上，别人访问不到。
> 关掉终端它就不在了；配置不会丢，因为配置都写在磁盘的 JSON 文件里。

---

### 第 1 步：把代码放到你自己的仓库

**如果你在 GitHub 上看到这个项目**：点右上角 **Fork**，一分钟搞定。

**如果你想从本地推上去**：

```bash
cd tech-weekly
git init && git add . && git commit -m "init"
git branch -M main
git remote add origin https://github.com/<你的用户名>/tech-weekly.git
git push -u origin main
```

---

### 第 2 步：在你自己的电脑上启动这个界面

界面是你本机跑的一个小程序，不是网页服务。需要先装 Python 3.10+
（[下载](https://www.python.org/downloads/)，安装时勾 **Add Python to PATH**）。

先确认你已经把代码弄到本地了（第 1 步 Fork 的话，就是 `git clone` 你自己的仓库）：

```bash
git clone https://github.com/<你的用户名>/tech-weekly.git
cd tech-weekly
python ui.py
```

终端会打印一行地址，浏览器自动打开 `http://127.0.0.1:8765`。
**这个地址只在你电脑上有效**，手机或其他电脑打不开——这是有意的，界面里有你的密钥。

想要独立的桌面窗口（没有地址栏，像个应用），用：

```bash
python app.py                    # 独立窗口
python app.py --install-shortcut # 顺便在桌面建个快捷方式，以后双击即用
```

---

### 第 3 步：在界面里填自己的邮箱和 Key（只存在本机）

**这一步只影响你本机**，改的是 `config.local.json`，不会动 GitHub 上的任何东西。
目的是让你能在本地预览、试发；第 4 步才把同样的值同步到 GitHub。

界面里两个页签：

**「收件与投递」**

| 字段 | 填什么 |
| --- | --- |
| SMTP 服务器 | 上表里对应的地址，例如 `smtp.163.com` |
| 端口 | 465（QQ / 163 / Gmail）或 587（Outlook） |
| 加密方式 | 465 选 SSL；587 选 STARTTLS |
| 发件人显示名 | 随便，例如「科技周报」 |
| **SMTP 账号** | 你的邮箱地址 |
| **SMTP 授权码** | 上一步申请的授权码 |
| 发件人地址 | 一般留空（默认等于 SMTP 账号） |

**「AI 与选稿」** → 「本机凭据 · 大模型 Key」→ 填 `sk-...`

> **为什么有两个「收件人」框？**
> 页签上方那个大框对应 `config.json`，**会被推到公开仓库**，所以请留空。
> 线上发信的收件人在下一步填，它会作为 Secret 保存，不会公开。

填完点「保存到本机」。**这一步会写两个文件，去向不同，要留意**：

| 你改的东西 | 存进哪个文件 | 会进仓库吗 |
| --- | --- | --- |
| SMTP 服务器、端口、加密方式、发件人显示名 | `config.json` | ✅ 会 |
| SMTP 账号、授权码、API Key | `config.local.json` | ❌ 不会（git 忽略） |

所以：**如果你改过 SMTP 服务器或端口（比如从 163 换成 QQ），一定要点界面底部的「提交并推送」**，
否则线上跑的还是配置里原来的 `smtp.163.com`。账号密码则走下一步的 Secrets。

改完想先看效果：

```bash
python main.py preview                    # 生成 HTML 预览，不发信
python main.py send --to 你的邮箱@qq.com   # 只发给自己，不进收件人列表
```

---

### 第 4 步：把同样的凭据同步到 GitHub（让自动任务能发信）

**为什么还要再来一次**：GitHub Actions 跑在 GitHub 的服务器上，读不到你电脑里的
`config.local.json`。它只认仓库的 **Secrets**。否则第 3 步填完，本地能发信，但每周自动跑的那次会失败。

界面的「部署到 GitHub」页签能一键完成：

1. 打开 [github.com/settings/tokens/new](https://github.com/settings/tokens/new)，选 **classic token**
2. **Scopes 勾上 `repo`**
3. 生成后复制 `ghp_...`，粘进界面的 Token 框
4. 仓库名会自动识别（从 `git remote` 读），确认一下
5. 填**收件人邮箱**（这一步必填）
6. 点「写入 Secrets」

写入的内容是 `LLM_API_KEY`、`SMTP_USER`、`SMTP_PASSWORD`、`MAIL_FROM`、`MAIL_TO` 这几项。
Token 只在内存里用一次，不会保存；写完可以直接去 GitHub 页面把它删掉。

---

### 第 5 步：在 GitHub 上验证一次（不是本机）

注意这一步在浏览器里打开 **github.com**，用的是你仓库的 Actions 页面，不是你本机的界面。

打开你仓库的 **Actions** → 左侧选 **Weekly Tech Digest** → 右侧 **Run workflow**。

| 跑哪个 | 作用 | 耗时 |
| --- | --- | --- |
| `llm` | 只翻译三条固定样例，验证 Key 和中文质量。不抓信源、不发信 | 十几秒 |
| `preview` | 走完整流程但不发信，产物在 artifact 里 | 约 1 分钟 |
| `run` | 真的发一封到你邮箱 | 约 1 分钟 |

先跑 `llm`，通过了再跑 `preview`，最后 `run`。

> 每次运行都会上传 `weekly-digest-*` artifact，点进去能下载这一期的 HTML 原件——
> 即使发信失败也能看到内容，排查很有用。

---

### 第 6 步：之后就自动了

工作流默认 **每周一 01:00 UTC（北京时间周一 09:00）** 自动跑。

想换时间，改 `.github/workflows/weekly.yml` 里的 cron：

```yaml
on:
  schedule:
    - cron: "0 1 * * 1"   # 分 时 日 月 周（UTC 时间，周一是 1）
```

| 你想要的北京时间 | 填进去的 cron |
| --- | --- |
| 周一 09:00 | `0 1 * * 1` |
| 周五 08:30 | `30 0 * * 5` |
| 周日 20:00 | `0 12 * * 0` |

---

## 四、用起来之后

> **先记住一件事：改完怎么生效。**
> 界面里点「保存」只是写本机的 `config.json`；要让 GitHub Actions 用上新配置，
> 还要点界面底部的 **「提交并推送」**。之后下一次定时任务（或你手动 Run workflow）才会按新配置跑。

### 改关注的主题

要改的是**每个领域那一行「关键词」**。位置：

```
界面顶部页签「领域与信源」
  └─ 找到对应领域的卡片（例如 🤖 AI / 大模型）
       └─ 卡片里第二行：「关键词（逗号分隔）」
```

**写法是逗号分隔的纯文本，不要加方括号和引号**。想关注智能体、多模态、大模型：

| | |
| --- | --- |
| ✅ 这样填 | `agent, 多模态, llm` |
| ❌ 不要这样 | `["agent", "多模态", "llm"]` |

方括号和引号是**手工编辑 `config.json` 时**才需要的（那是 JSON 数组的语法）。
在界面里填只会把 `["agent"` 当成一个完整的关键词，等于写错。
（真粘进去了也不用慌，界面保存时会自动把方括号和引号洗掉。）

拿 `AI / 大模型` 举例，改前是一长串，改后就三个：

- **改前**：`llm, 大模型, 大语言模型, agent, 智能体, transformer, diffusion, 多模态, multimodal, openai, …`
- **改后**：`agent, 多模态, llm`

改完点底部「保存」，再点「提交并推送」。

关键词影响两件事：**规则打分加分**，以及部分信源的**硬过滤**（HN 默认要求标题至少命中 2 个）。

> 如果改完还是觉得"偏"，光调关键词不够——关键词只是打分，决定不了取舍。
> 更有效的两个动作：**加更精准的信源**，或者把「AI 与选稿」里的**相关度阈值**调高。

### 加一个新信源

在「领域与信源」页，点对应领域下面的 **+ 添加信源**，选类型、填名字和地址，保存。

加完**立刻点那一行的「测试」按钮**——它会真的去抓一次，告诉你返回了几条、样例标题是什么。
如果返回 0 条或报错，说明地址不对或该源挂了，别急着提交。

常见的几种：

| 类型 | 你要填的 |
| --- | --- |
| RSS / Atom | 订阅地址（以 `.xml` 或 `/feed` 结尾） |
| arXiv | 分类，例如 `cs.AI`、`cs.RO`、`q-bio.QM` |
| Hacker News | 搜索关键词，一行一个 |
| Bing 新闻 | 搜索关键词，一行一个（国内可直连，无需 Key） |
| PubMed | 检索式，适合盯一个具体医学主题 |
| Hugging Face | 只要条数 |

### 删信源 / 临时停用

- **删**：那一行的「删除」按钮
- **临时停用**（想留着以后恢复）：展开「高级字段」，把 `"enabled": false` 加进去

目前有两个源是停用状态：`Solidot`（连接被拒）和 `网页搜索`（需要先配 Key）。

### 换收件人、换发信邮箱

- **只换收件人**：重做一次第 4 步，`MAIL_TO` 填新地址即可
- **整个换发信邮箱**：在「收件与投递」里改 SMTP 账号和授权码，保存后再走一次第 4 步

### 改条数和时间窗口

「AI 与选稿」页签里：

- **时间窗口（天）**：默认 10。改小会更"当周"，改大会更容易覆盖到稍早的重要发布
- **每领域条数** / **总条数上限**：默认 6 / 24

### 想省钱

按花费从低到高排：

1. **关掉大模型选稿**：「AI 与选稿」→ 取消勾选「启用大模型选稿」，省掉约 0.03 元/期。
   代价是选稿退回规则打分，"该不该上"的判断会弱一些。
2. **关掉中文摘要**：把 `config.json` 里的 `"llm": { "enabled": false }` 改成 `false`
   （界面里没有这个开关）。或者本地运行时用 `python main.py preview --no-llm`，
   临时不调模型。
3. **完全不要 Key**：不配 `LLM_API_KEY`，程序会自动保留英文原文，0 成本。

### 新增一个领域

除了改现有领域，也可以整个加一个。界面里点「+ 添加领域」，然后填：

| 字段 | 说明 |
| --- | --- |
| 名称 | 显示在邮件小节标题里，例如「新能源 / 储能」 |
| 图标 | 一个 emoji，例如 🔋 |
| id | 小写英文，例如 `energy`（状态文件按这个记） |
| 每期条数 | 这个领域每期几条 |
| 关键词 | 逗号分隔，参与打分和过滤 |

加完之后在它下面「+ 添加信源」，填两三个源就够跑起来了。想用 JSON 直接写的话：

```jsonc
{
  "id": "energy", "name": "新能源 / 储能", "icon": "🔋",
  "enabled": true, "max_items": 5,
  "keywords": ["battery", "电池", "solar", "光伏", "fusion", "储能"],
  "sources": [
    { "type": "rss", "name": "CleanTechnica", "url": "https://cleantechnica.com/feed/", "weight": 10 }
  ]
}
```

### 某个信源一直失败

程序会自动处理：连续失败 4 期就暂停抓取 21 天，期间用上期快照补位，并在邮件底部标注。
如果你确认源已经修好、不想等 21 天：

```bash
python main.py reset --source "Bing 新闻"
python main.py reset --all
```

---

## 五、它替你兜住了哪些坑

周报是个"每周都要跑、偶尔出问题"的东西。下面这些情况都处理过了：

| 情况 | 它会怎么做 |
| --- | --- |
| 某个信源这周挂了 | 用**上期抓到的内容**补位，邮件底部注明「已用上期快照补 N 条」，栏目不会空着 |
| 某个信源改了版 / 一直失败 | 连续 4 期失败后暂停 21 天，到期自动重试 |
| 同一件事被十家媒体转载 | 只留一条，其余作为「相关」链接 |
| 同一件事各家标题不一样 | 中文用字符相似度、英文用词集合相似度合并；再用大模型判断"是不是同一事件" |
| 模型返回的 JSON 被截断 | 明确报错并跳过这批，这批保留英文 |
| 翻译整批失败 | 只影响那几条，其余照常，周报照发 |
| **所有条目都没翻成** | **中止发信**，避免你收到一封"全英文的中文周报" |
| 一条内容都抓不到 | 不发空邮件 |
| 同一内容下期又出现 | 命中缓存，不重复调用模型、不重复付费 |

---

## 六、花多少钱

一期（约 98 条送审、33 条入选）：

| 环节 | 花费 |
| --- | --- |
| 大模型选稿（60 条打分 + 去重） | 约 ¥0.03 |
| 中文摘要（33 条，分 4 次请求） | 约 ¥0.02 |
| **合计** | **约 7 分钱 / 期** |

一周一期，**一年约 3.5 元**。有缓存，同一篇内容不会重复计费；重复运行更便宜（实测第二次只要 ¥0.006）。

GitHub Actions 对公开仓库免费，私有仓库每月 2000 分钟额度，这个项目每次跑 30–60 秒，完全够用。

---

## 七、常见问题

### 收不到邮件

1. **先看 Actions 有没有失败**：仓库 → Actions → 点进最近一次运行
2. **看 artifact**：即使发信失败，`weekly-digest-*` 里也有生成的 HTML
3. **查垃圾箱**：自建发信偶尔被判为推广邮件，把发件人加进白名单

### 提示 `535 Authentication failed`

九成是把**登录密码**当授权码用了。QQ / 163 / Gmail 都必须用单独申请的授权码。

### 提示 `Connection unexpectedly closed` / 卡在连接

端口和加密方式不匹配：

- **465** → 加密方式选 **SSL**
- **587** → 加密方式选 **STARTTLS**

### 邮件里全是英文，没有中文摘要

| 现象 | 原因 |
| --- | --- |
| 日志说「api_key 缺失」 | Secrets 里没配 `LLM_API_KEY`，或写失败了 |
| 报 `401 Authentication Fails` | Key 写错、被删、或余额不足 |
| 报 `400 Invalid model` | 模型名不对，`config.json` 里应该是 `deepseek-flash` |
| 只有个别几条是英文 | 那几条翻译失败，程序保留了原文；下一期会自动重试 |

排查最快的方式：Actions 里跑一次 `llm` 模式，它只翻译三条固定样例，几秒就能看出 Key 有没有问题。

### 内容不像我想要的

按这个顺序调：

1. **改关键词**：领域里的 `keywords` 决定加分和过滤
2. **调选稿阈值**：「AI 与选稿」→ 相关度阈值（默认 6.0）。调高更严格，调低更宽松
3. **加更精准的信源**：比如只想要 BCI，就加 PubMed + Bing 新闻的专项源
4. **看是不是比例问题**：每领域条数可以单独调

### Actions 到点没跑 / 跑晚了

GitHub 的定时任务在高峰期会延迟几分钟到几十分钟，这是官方行为。

另外注意：**仓库如果 60 天没有任何提交，GitHub 会自动停用定时任务**。点一下 Actions 页面的
「Enable workflow」就能恢复。

### 日志里有一堆 Node.js 20 deprecated 警告

那是 GitHub 官方 Action 的弃用提示，不影响运行，可以忽略。

### 界面打不开 / 打开是空白

- 端口被占：`python ui.py --port 9000`
- 想确认服务在跑：浏览器打开 `http://127.0.0.1:8765/api/config`，应该返回一段 JSON

---

## 八、进阶

### 8.1 完全在本机跑（不依赖 GitHub）

```bash
python main.py test                 # 体检全部信源
python main.py llm                  # 测 Key 和中文质量
python main.py preview --days 7     # 生成 out/ 下的预览，不发信
python main.py send                 # 生成并发送
python main.py run                  # 先试发 TEST_RECIPIENT，成功后再发全体
```

Windows 也可以用附带的脚本：

```powershell
.\run-weekly.ps1 -Mode llm        # 中文摘要自检
.\run-weekly.ps1 -Mode preview    # 只预览
.\run-weekly.ps1                  # 按配置发送
```

凭据的读取优先级：

```
环境变量  >  config.local.json（界面保存的）  >  config.json
```

所以界面上填的值本机就能直接用；环境变量适合临时覆盖。

想让它每周自动跑又不依赖 GitHub，可以用 Windows 任务计划程序：
操作 → 创建基本任务 → 每周一 09:00 → 启动程序 → `pythonw.exe` → 参数填
`C:\路径\tech-weekly\main.py send`，起始于填项目目录。

### 8.2 命令行参考

| 命令 | 作用 |
| --- | --- |
| `python main.py test` | 体检所有信源，报告哪些挂了 |
| `python main.py llm` | 只测中文摘要（约 ¥0.001） |
| `python main.py preview` | 生成预览不发信 |
| `python main.py preview --no-llm` | 生成预览，完全不调模型 |
| `python main.py send` | 生成并发送 |
| `python main.py run` | 先试发，成功后再发全体 |
| `python main.py reset --source "名字"` | 解除某个信源的暂停 |
| `python main.py reset --all` | 解除全部暂停 |

常用参数：`--days 7`（时间窗口）、`--outdir out`（输出目录）、
`--to a@x.com,b@x.com`（临时收件人，不落盘）、`--no-llm`（不调模型）、
`--config 路径`（换配置文件）。

退出码：`0` 成功；`2` 本期没有入选条目（不发空邮件）；
`3` 配了 Key 但中文全部失败（不发全英文邮件）。

### 8.3 配置文件 `config.json`

一般不需要手改（界面能改大部分），但知道结构有助于微调：

```jsonc
{
  "report": {
    "title": "科技周报",
    "window_days": 7,        // 抓最近几天
    "max_per_domain": 6,     // 每领域默认条数（领域内可覆盖）
    "max_total": 24,
    "intro": "……",           // 邮件开头那段说明
    "footer": "……"
  },
  "smtp": {
    "host": "smtp.163.com", "port": 465, "ssl": true, "starttls": false,
    "user": "",              // 留空：从环境变量 / config.local.json 读
    "password": "",
    "from_name": "科技周报",
    "sender": "",
    "recipients": [],        // 公开仓库里请留空，用 MAIL_TO Secret
    "cc": []
  },
  "fetch": {
    "timeout_seconds": 25, "workers": 10, "retries": 2,
    "snapshot_fallback": true,     // 源挂了用上期快照补位
    "disable_after_failures": 4,   // 连续失败几期后暂停
    "cooldown_days": 21            // 暂停多久后重试
  },
  "curation": {
    "enabled": true,
    "score_threshold": 6.0,        // 0-10 的相关度阈值
    "pool_per_domain": 12,         // 每领域送审条数
    "min_keep_per_domain": 4,      // 每领域保底
    "topic_dedup": true,           // 合并同一事件
    "budget_cny": 0.1              // 本环节花费上限
  },
  "llm": {
    "enabled": true,
    "base_url": "https://api.deepseek.com",
    "api_key": "",                 // 留空：读 LLM_API_KEY
    "model": "deepseek-flash",
    "thinking": "disabled",        // DeepSeek V4 默认开思考模式，翻译不需要
    "batch_size": 10,
    "max_tokens": 4096,
    "use_cache": true
  },
  "domains": [
    {
      "id": "ai", "name": "AI / 大模型", "icon": "🤖",
      "enabled": true, "max_items": 6,
      "keywords": ["llm", "agent", "多模态"],
      "sources": [
        { "type": "rss", "name": "显示名", "url": "https://…/feed", "weight": 12 }
      ]
    }
  ]
}
```

`weight` 是来源权重（越大越容易排在前面）。`group` 用来在领域内划子分组（脑机接口就是这么做的）。

### 8.4 信源类型与字段

| 类型 | 必填 | 可选常用字段 |
| --- | --- | --- |
| `rss` | `url` | `weight`、`title_keywords`、`filter_keywords` |
| `arxiv` | `categories` | `queries`、`keywords`、`weight` |
| `hackernews` | `queries` | `hn_keywords`、`min_keyword_hits`、`weight` |
| `huggingface_papers` | — | `limit`、`weight` |
| `pubmed` | `queries` | `days`、`retmax`、`title_keywords`、`filter_keywords` |
| `bing_news` | `queries` | `title_keywords`、`filter_keywords`、`group` |
| `web_search` | `provider` | `api_key`、`queries`、`limit`、`days` |

所有信源都支持：`name`、`weight`、`enabled`、`group`、
`user_agent`、`referer`、`timeout_seconds`、`retries`（后四个覆盖全局抓取设置）。

**信源级精度控制**（防止"顺带提一句"的内容混进来）：

```jsonc
"title_keywords": ["brain-computer", "bci", "eeg"],  // 标题必须命中
"min_title_hits": 1,
"filter_keywords": ["neural interface", "eeg"],      // 标题+摘要再要求命中
"min_filter_hits": 2
```

只按命中总数过滤会误杀——真正做这个主题的论文，标题已经把话说清楚了，所以**标题检查最有效**。

**网页搜索需要 Key**（Bing Web Search API 已退役，不再支持）：

| provider | Key 环境变量 | 免费额度 |
| --- | --- | --- |
| `brave` | `BRAVE_API_KEY` | 有免费层 |
| `tavily` | `TAVILY_API_KEY` | 每月免费额度 |
| `searxng` | `SEARXNG_URL`（自建实例地址） | 免费 |
| `google_cse` | `GOOGLE_API_KEY` + `GOOGLE_CSE_ID` | 每天 100 次 |

### 8.5 微信公众号

微信没有官方 RSS。本项目通过第三方公益服务 [wechat2rss](https://wechat2rss.xlab.app)
拿到公众号内容，用的就是普通 `rss` 类型：

```jsonc
{ "type": "rss", "name": "量子位（公众号）", "weight": 14,
  "url": "https://wechat2rss.xlab.app/feed/<hash>.xml" }
```

已接入：量子位、机器之心、新智元、PaperWeekly、阿里技术。

**局限**：该服务只收录它自己在抓的 300+ 个公众号，你没法随意添加任意公众号。
想要某个号，只能去[它的仓库](https://github.com/ttttmr/wechat2rss/issues)推荐收录。

### 8.6 内部机制（想改代码时看）

1. **并发抓取**：线程池按 `workers` 拉全部信源。重试只针对"可能自己好起来"的错误
   （超时、连接断开、5xx、429）；**403 / 404 不重试**，白跑还拖慢整轮。
2. **快照回退**：每个源最近一次成功的条目存在 `cache/source-state.json`，
   挂了就用它补位。状态按「领域\|名字」记（不同领域可能有同名信源）。
3. **去重三层**：URL 归一化 → 标题完全相同 → 近似去重（中文用字符 2-gram 余弦 0.39，
   英文用词集合 Jaccard 0.5）。英文不能用字符 n-gram：任意两段英文都共享大量字母组合，
   实测两篇无关长标题余弦也有 0.41，会把不同事件误合并。
4. **规则打分**：`来源权重 × 时间衰减 + 关键词命中 + 论文加分 + HN 热度`，
   再对诉讼八卦（−14）、财经融资（−4.5）降权。
5. **大模型选稿**：对每领域候选按一套编辑标准打 0–10 分，低于阈值丢弃，
   再合并同一事件。**任何失败都退回规则分**，不影响发信。
6. **配额与打散**：按 `max_items` 取前 N 条，取的时候轮转不同来源，避免整节被一个源包场；
   子分组按信源比例分名额，不够时互相回收。
7. **渲染与发送**：HTML + 纯文本双版本（`multipart/alternative`），
   SSL/STARTTLS 由配置决定，主题和发件人中文名按 RFC 2047 编码。

### 8.7 自检与测试

```bash
python main.py test                 # 信源体检
python main.py llm                  # 中文摘要自检（花约 ¥0.001）
python tests/test_nacl.py           # 加密实现（RFC 7748 向量）
python tests/test_config.py         # 配置合并、BOM、优先级
python tests/test_fetch.py          # 抓取层重试策略、按源覆盖、Bing 解析
python tests/test_health.py         # 信源健康度与快照回退
python tests/test_dedup.py          # 标题相似度与选稿解析
python tests/test_translate_flow.py # 中文摘要端到端（本地假模型）
python tests/test_web_search.py     # 网页搜索（本地假服务）
python tests/test_smtp_flow.py      # 发信链路（本地假 SMTP）
```

除 `test_nacl` 的交叉验证部分（需要 PyNaCl，缺失时自动跳过）外，全部离线、不花钱。

### 8.8 换模型 / 换服务商

任何 OpenAI 兼容接口都能用，改三行：

```jsonc
"llm": {
  "base_url": "https://api.deepseek.com",   // Kimi: https://api.moonshot.cn/v1
  "model": "deepseek-flash",                // 通义: qwen-plus / OpenAI: gpt-4o-mini
  "api_key": ""                             // 留空则读环境变量
}
```

如果服务商不支持 `response_format: json_object`，删掉 `translate.py` 里 `_chat()` 中那一行即可——
解析函数本身能容忍代码块和前后废话。

---

## 九、文件结构

```
tech-weekly/
├─ main.py                       # 主程序：抓取 / 去重 / 排序 / 渲染 / 发信 / CLI
├─ translate.py                  # 中文摘要：批量请求、缓存、计费、容错解析
├─ curate.py                     # 大模型选稿：相关度打分、主题去重、预算闸门
├─ health.py                     # 信源健康度：失败计数、上期快照、冷却停抓
├─ ui.py / ui.html               # 本地配置界面（http.server + vanilla JS，零依赖）
├─ app.py                        # 桌面启动器：独立窗口 + 桌面快捷方式
├─ nacl_lite.py                  # 纯 Python 的 sealed box，用于写 GitHub Secrets
├─ config.json                   # 共享配置：领域、信源、关键词、模型参数
├─ config.local.json             # 本机私有凭据（git 忽略）
├─ run-weekly.ps1                # Windows 本地一键运行
├─ docs/参考项目调研.md            # 同类开源项目对比与改造清单
├─ tests/                        # 8 个测试文件，见 8.7
├─ .github/workflows/weekly.yml  # GitHub Actions 定时任务
├─ cache/                        # 摘要缓存 + 信源状态（git 忽略）
└─ out/                          # 生成的预览 HTML/TXT（git 忽略）
```

---

## 十、说明与边界

- 只读取各站点公开的 RSS 和开放 API（含 PubMed E-utilities），**不抓取需要登录的内容**。
  请遵守各信源的使用条款。
- **微信公众号通过第三方服务 wechat2rss 转成 RSS**，不是微信官方接口。该服务挂了只影响那几个源。
- **中文摘要由大模型生成，可能与原文有偏差**。重要信息请点开原文核对，英文原标题一直保留在邮件里。
- **不要把凭据填进 `config.json` 再推到公开仓库**。用界面的「本机凭据」或 GitHub Secrets。
- `nacl_lite.py` 是纯 Python 实现的密码学代码，存在时序侧信道、没有内存清零。
  它只用于加密短小的 API Key 且在本机一次性执行，**不要拿它保护高价值长期密钥**。
