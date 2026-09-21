# 苏丹的游戏 · 本地存档修改器

一个面向《苏丹的游戏》（Sultan's Game）的本地可视化存档修改器。启动后提供 Web 界面，全部读写都发生在本机，不需要安装第三方 Python 包。

> 非官方工具。修改存档前请退出当前对局并保留备份；请勿在 Steam Cloud 正在同步时同时修改同一份存档。

## 功能

- 自动识别 Steam 账号目录、自动存档、手动槽位与最近回合快照。
- 正确显示游戏界面槽位与磁盘文件的对应关系，例如槽位 001 对应 `USERARCHIVE/000.json`。
- 只读查看基础属性、集合规模、剧情计数器与全局计数器，并支持搜索。
- 按袋位分组预览当前手牌和背包卡牌，显示品级、描述、标签、数量与实例位置。
- 汇总当前有效卡牌容器，解析并筛选手牌、已装备、事件/仪式槽、苏丹卡池、阅读、锁定、特殊吸附和已失去记录；关联对象会显示角色、装备位、仪式名与槽位。
- 一键移除谗言卡及已生成的“苏丹的戏弄”仪式。
- 从游戏当前版本的卡牌配置中搜索并添加任意卡牌。
- 自动为新增卡牌分配有效手牌位置，也可将未进入手牌的实例放入手牌。
- 修改卡牌数量或移除卡牌实例。
- 可选同步第一槽、自动存档与同回合快照。
- 每次写入前自动备份，并支持从界面恢复。

## 环境要求

- macOS（当前主要支持平台）
- 已通过 Steam 安装《苏丹的游戏》
- Python 3.11 或更高版本

程序会自动查找 macOS 和常见 Linux Steam 路径。非默认安装位置可通过命令行参数指定。

## 快速开始

克隆仓库后进入项目目录：

```bash
git clone https://github.com/SuperPrintf/sultans-game-save-editor.git
cd sultans-game-save-editor
python3 server.py --open
```

也可以在 macOS Finder 中双击 `start.command`。服务默认仅监听：

<http://127.0.0.1:8765/>

按 `Ctrl+C` 停止服务。

## 自定义路径

游戏或存档不在默认位置时：

```bash
python3 server.py --open \
  --game-root "/path/to/Sultan's Game" \
  --save-base "/path/to/SAVEDATA"
```

也可以设置环境变量 `SULTANS_GAME_ROOT` 指定游戏安装目录。

## 推荐使用流程

1. 完全退出游戏，或至少返回标题界面，避免游戏内存中的旧数据覆盖磁盘文件。
2. 如 Steam Cloud 正在同步，等待同步结束；发生覆盖冲突时可暂时关闭该游戏的云同步。
3. 启动修改器并选择目标存档。
4. 完成修改后重新加载该存档，确认结果无误。
5. 若结果异常，在“备份与恢复”页面还原修改前版本。

## 安全设计

- HTTP 服务只允许绑定 `127.0.0.1`、`localhost` 或 `::1`。
- 拒绝跨来源写入和目录越界访问。
- 所有 JSON 写入使用同目录临时文件和原子替换。
- 每次修改前都会在 `backups/` 中创建完整备份。
- `backups/`、Python 缓存和本机运行数据均被 Git 忽略，不会进入仓库。

## 项目结构

```text
.
├── server.py              # 本地 HTTP 服务、存档解析与修改逻辑
├── start.command          # macOS 双击启动脚本
├── static/
│   ├── index.html         # Web 界面
│   ├── app.js             # 前端交互
│   └── styles.css         # 页面样式
├── tests/
│   └── test_core.py       # 核心逻辑与备份同步测试
└── .github/workflows/
    └── test.yml           # GitHub Actions
```

## 测试

```bash
python3 -m py_compile server.py
python3 -m unittest discover -s tests -v
node --check static/app.js  # 可选，需要 Node.js
```

## 说明

本项目不会连接游戏服务器，也不会绕过 Steam 或游戏本体。它只编辑当前用户本机上的 JSON 存档文件。游戏更新可能改变存档结构或卡牌配置路径，使用前请始终保留备份。
