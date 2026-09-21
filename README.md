# 超星学习通自动刷课脚本 (Playwright + DeepSeek AI 深度改造版)

> 基于 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) 深度改造
>纯AI（deepseek）改写代码
> 本项目遵循 **GPL-3.0** 开源协议

[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

---

## ⚠️ 免责声明

- 本项目仅供**个人学习交流**使用，**严禁**用于商业盈利、代刷、售卖等任何违反平台规定的行为。
- 使用本项目造成的一切后果（包括但不限于账号封禁、成绩取消、纪律处分）由使用者**自行承担**，开发者不承担任何责任。
- 请遵守所在学校/平台的用户协议，合理使用自动化工具。
- 本项目基于 GPL-3.0 协议开源，**禁止**将修改后的代码作为闭源商业软件发布或销售。

---

## 与原版的区别

本 fork 在原 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) 的基础上做了**大量深度改造**，核心差异如下：

| 维度 | 原版 | 本 Fork |
|---|---|---|
| **题目抓取** | requests 解析 HTML | **Playwright 真实浏览器渲染**，解决 JS 动态注入字体导致的乱码 |
| **答题引擎** | 传统题库（言溪等） | **DeepSeek V4 AI 答题**，内置深度思考模式开关 |
| **闯关模式** | 遇到"章节未开放"直接回滚，容易死循环 | **智能解锁链**：空章节自动点击解锁、等待服务端延迟、自动补刷 |
| **视频卡 100%** | 无限重试上报 | 最多 6 次复核 + 交叉验证任务点状态，**不再死等** |
| **风控对抗** | 无 | **ddddocr 自动识别验证码**，403 自动重试 |
| **凭证管理** | cookies.txt 独立文件 | 账号 + cookies 合并至 `credentials.json`，**可分别删除** |
| **登录稳定性** | 无超时，网络抖动卡死 | 全部请求补全 timeout，cookie 失效自动重登 |
| **视频进度条** | 刷屏、折行 | 按终端宽度自动裁剪，**原地刷新不刷屏** |
| **课程选择** | 仅英文逗号 | 支持**中英文逗号、顿号、分号、空格**等 8 种分隔符 |
| **启动方式** | `python main.py` | **双击 `start.bat`** 全自动（建环境、装依赖、下载浏览器内核） |
| **配置持久化** | config.ini | `settings.json` 自动保存，菜单改完即存 |

---

## 功能特性

### 核心能力
- ✅ **视频 / 音频**自动播放（支持倍速）
- ✅ **文档 / 阅读 / 问卷**自动完成
- ✅ **章节测验 / 作业**自动答题提交
- ✅ **直播任务**自动刷时长
- ✅ **闯关模式**智能解锁，自动处理空章节
- ✅ **章节学习次数**批量增加

### AI 答题
- DeepSeek V4-Flash / V4-Pro 可选
- **深度思考模式**可在菜单中随时开关
- 自动读取 `prompt_cache_hit_tokens`，实时显示缓存命中率
- 答案格式自动清洗，最大程度降低"返回格式不匹配"导致的失败

### 稳定性
- Ctrl+C 优雅退出（**不要点窗口 X 按钮**）
- 网络异常简洁提示，不刷屏
- 视频 403 自动过验证码后重试
- Cookie 失效自动用账号密码重登
- 章节未开放自动等待 + 补刷，无需手动重启

---

## 快速开始

### 环境要求

- **Windows** 系统
- **Python 3.9 ~ 3.11**（3.12+ 可能因 `ddddocr` 依赖问题装不上）
- 安装 Python 时勾选 **"Add python.exe to PATH"**
- 能访问外网（首次需要下载依赖约 100-200MB + Playwright 浏览器内核约 150MB）

### 方式一：一键启动（推荐）

1. 下载本仓库所有文件（**保持目录结构完整**）
2. 确保以下文件都在同一目录下：
main.py
start.bat
start.py
requirements.txt
deepseek_api.py
api/
resource/
3. **双击 `start.bat`**
4. 首次运行会自动：
- 创建虚拟环境 `.venv`
- 安装所有 Python 依赖
- 下载 Playwright 浏览器内核
- 启动主程序
5. 按提示输入账号密码，选择课程，开始刷课

> 首次启动可能需要 5-10 分钟（取决于网速），之后每次启动只需几秒。

### 方式二：手动运行

# 1. 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 安装 Playwright 浏览器
playwright install chromium

# 4. 运行
python main.py


## 配置说明

### 菜单项
1. 开始刷课
2. 管理API密钥          （设置/删除 DeepSeek API Key）
3. 管理保存账号        （删除账号密码 / 删除Cookies / 全部删除）
4. 深度思考模式        （开启/关闭，不推荐开启）
5. 未开放章节处理      （策略/重试/补刷轮数/回滚上限）
6. 解锁等待            （等待次数/间隔秒数）
7. 补刷等待            （全刷完后补刷的轮数和间隔）
8. 退出

### settings.json 配置项
配置项	默认值	说明
enable_thinking	false	深度思考模式（不推荐开启）
locked_chapter_policy	auto	auto 自动 / skip 直接跳过 / rollback 回滚解锁
locked_retry_times	2	未开放章节重试次数
locked_retry_interval	5	重试间隔（秒）
locked_second_pass_rounds	3	全部刷完后补刷轮数
max_rollback_times	3	同一章节最大回滚次数
unlock_check_times	4	解锁等待重读次数
unlock_check_interval	5	解锁等待间隔（秒）
sweep_wait_seconds	180	补刷轮次间等待（秒）

## 关于 DeepSeek API Key
在 DeepSeek 开放平台 注册后可获取 API Key。
程序会在菜单里引导你设置，保存在 api_key.json
建议使用 deepseek-v4-flash（便宜且够用）
深度思考模式不推荐开启：实测对刷课选择题的正确率反而下降，且耗时增加 3-10 秒/题

## 常见问题
Q：点击窗口 X 按钮会卡死？
A：这是 Windows 机制导致的。请用 Ctrl+C 退出，程序会优雅清理。按一次 Ctrl+C 等 5 秒内自动退出；再按一次立即强退。

Q：首次启动很慢？
A：正常。首次需要下载依赖和浏览器内核，等一次就好。

Q：换电脑后提示"课程列表为空"？
A：大概率是登录没成功。把老电脑上的 credentials.json 复制到新电脑同一目录，就能用保存的 cookies 直接登录。

Q：视频卡在 100% 不动了？
A：超星服务端延迟判定问题。新版已经会最多上报 6 次 + 复核任务点状态，确认没完成会跳过继续刷后面的章节，不再死等。如果视频被反作弊标记（比如拖过进度条），建议在浏览器里正常播放一遍，再重新运行脚本。

Q：题目乱码？
A：确认 resource/font_map_table.json 存在。这个文件是超星加密字体的解密对照表，不能少。

Q：提示"缺少依赖"但明明装了？
A：检查是否在虚拟环境里。用 start.bat 启动会自动激活 .venv。

## 许可证
本项目遵循 GNU General Public License v3.0 (GPL-3.0)。

Copyright (C) 2024 Samueli924
Copyright (C) 2026 [你的名字]
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.
You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

## 致谢
原项目：Samueli924/chaoxing
字体解密算法：SocialSisterYi
验证码识别：ddddocr
题库框架参考：sz134055
代码帮助：deepseek

⭐ 如果这个项目帮到了你，欢迎给个 Star 支持一下！