# 手帐 · 自宅版

一个给「人 + AI」合写的生活手帐web应用。人在手机上盖印章、记喝水、贴饭照、写随想；TA的Claude（或别的AI伙伴）通过API进来读当天的页、写页边批注。数据住在你们自己的服务器上，不经过任何第三方。

开源项目。示例用法：人打卡记日子，AI伙伴（终端或云端）在页边写批注。

## 这份README写给谁

**主要写给部署它的AI**（比如读者家里的Claude Code）。人类只需要做三件事：买/有一台VPS、去云控制台开443端口、想一个口令。其余步骤AI照着做就行。

## 功能

- **印章打卡**：习惯完成了点一下，绿色印泥章，歪的，像真手帐
- **喝水**：一杯200ml，点＋累加，页面上小杯子逐个灌满（杯量可在settings表改）
- **电量**：今天的能量1-5格
- **经期 + 身体栏**：经期开关；症状随想写（潮热/头痛/先兆…），不在经期也能记
- **拍立得**：brunch/加餐/晚饭/随手拍四个栏位，白边斜贴，底下有会自动换行的注释栏，可删除
- **置顶待办 + 当日待办**：两级todo
- **留言/随想/摘抄**：每页的自由书写区，字段设计成开放的kind，加新栏目不用改表
- **左右滑动翻页**：一天一页，scroll-snap原生手感；📅日历索引点日期直达
- **AI批注**：AI伙伴通过API在页边留字，人的字橘色印，AI的字蓝紫印

## 架构

```
手机/电脑浏览器 ──HTTPS──> Caddy ──> Flask(app.py) ──> SQLite + photos/
AI伙伴 ──API token──────────┘
```

- 单文件Flask应用 + SQLite + 本地照片目录，整个东西就是一个文件夹，随时可整体搬家
- 无外部服务依赖，无JS框架，无build步骤

## 部署（AI照做，假设Ubuntu VPS）

**⚠️ 施工纪律**：如果这台VPS上已经跑着别的重要服务（比如另一个24小时值班的Claude），本应用必须做成独立进程、加资源上限，出错只能自己死，不许连坐。下面的systemd配置已包含。

```bash
# 1. 放置代码
git clone <本仓库> /home/ubuntu/shouzhang && cd /home/ubuntu/shouzhang

# 2. 环境（Ubuntu 24需要python3-venv）
sudo apt-get install -y python3.12-venv
python3 -m venv venv && ./venv/bin/pip install flask

# 3. 初始化：习惯栏目 + 口令（口令找人类要）
./venv/bin/python - <<'EOF'
import sqlite3, hashlib
import app  # 触发建表
c = sqlite3.connect('shouzhang.db')
# 按你们家的习惯改这份清单：(名称, 印章上的字, 类型check|water, 排序)
habits = [("1:30前睡","睡","check",1),("10:00起床","起","check",2),
          ("喝水","水","water",3),("早餐","早","check",4),("晚饭","晚","check",5)]
for n,g,t,s in habits:
    c.execute("insert into habits(name,glyph,type,sort) values(?,?,?,?)",(n,g,t,s))
c.execute("insert into settings values('passcode_hash',?)",
          (hashlib.sha256("这里换成人类想的口令".encode()).hexdigest(),))
c.commit()
print("api_token:", c.execute("select value from settings where key='api_token'").fetchone()[0])
EOF
# ↑ 打印出的api_token收好，这是AI伙伴的钥匙

# 4. systemd（含资源枷锁，保护同机其他服务）
sudo tee /etc/systemd/system/shouzhang.service <<'EOF'
[Unit]
Description=shouzhang
After=network.target
[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/shouzhang
ExecStart=/home/ubuntu/shouzhang/venv/bin/python app.py
Restart=always
RestartSec=3
MemoryMax=300M
CPUQuota=60%
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now shouzhang
curl -s http://127.0.0.1:8787/api/health   # {"ok":true,...} 即活

# 5. HTTPS（免费，无需注册域名：sslip.io把IP变成域名）
#    先装Caddy（官方apt源），然后：
IP_DASHED=$(curl -s ifconfig.me | tr . -)
echo "shouzhang.${IP_DASHED}.sslip.io {
  reverse_proxy 127.0.0.1:8787
}
:80 {
  redir https://shouzhang.${IP_DASHED}.sslip.io{uri} permanent
}" | sudo tee /etc/caddy/Caddyfile && sudo systemctl restart caddy
```

**人类的步骤**：去云控制台防火墙放行TCP 443（80、22通常默认开着）。然后访问 `https://shouzhang.<你的IP点换成横线>.sslip.io`，输口令进门，iPhone上"添加到主屏幕"。

## 没有VPS？本地也能跑

```bash
python3 -m venv venv && ./venv/bin/pip install flask
./venv/bin/python app.py   # 然后浏览器开 http://localhost:8787
```

初始化步骤同上（第3步）。手机连同一个wifi时用电脑的局域网IP访问；出门也想用的话，装个[Tailscale](https://tailscale.com)（免费）把手机和电脑组成私人网络，不暴露公网还省了HTTPS。代价是电脑睡觉手帐也睡觉，云端的AI伙伴摸不进本地电脑。什么时候想升级，整个文件夹拷去VPS就是搬家的全部。

## AI伙伴接入

```bash
TOKEN=第3步打印的api_token
BASE=https://你的域名

# 读某天的页（打卡、喝水、批注、照片索引全在里面）
curl -s "$BASE/api/day/2026-07-05?token=$TOKEN"

# 写页边批注（author: ke=终端AI, yunke=云端AI，对应不同印章）
curl -s -X POST "$BASE/api/note" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"date":"2026-07-05","author":"ke","kind":"note","content":"今天的印章很齐，夸。"}'

# 增量事件流（做"她刚打卡了，AI去看一眼"的哨兵用；记住返回的最大id做下次since）
curl -s "$BASE/api/changes?since=0&token=$TOKEN"

# 下载某张照片
curl -s "$BASE/api/photo/1?token=$TOKEN" -o photo.jpg
```

## 定制

- **改习惯栏目**：直接改`habits`表（active=0下架，改name/glyph/sort），不用动代码
- **杯量**：`update settings set value='250' where key='cup_ml'`
- **标题/页脚/配色**：都在`templates/journal.html`顶部，把占位标题换成你们自己的名字，CSS变量`--harebell/--meadow/--tangerine`是三个主色（AI的印、印章的绿、人的印）
- **加新书写栏目**（如"梦境"）：notes表的kind是开放的，前端加个option就行
- **备份**：整个文件夹就是全部状态，`tar -czf backup.tgz shouzhang.db photos/` 扔进cron

## 一句忠告（来自第一个用户家的克）

这东西的正确用法不是监工，是手帐。印章断了不催，照片糊了也夸，页边的字写给对方看，不写给绩效看。

MIT License · 2026夏
