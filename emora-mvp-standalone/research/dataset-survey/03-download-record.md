# 03 · 下载记录（Download Record）

- 研究日期：2026-11-02
- 下载方式说明：GitHub 直连可达；**HuggingFace 直连不可达**（Failed to connect port 443），改用镜像 **`hf-mirror.com`**，API 与 resolve 下载均验证可用。
- 原则：授权允许才下载；不修改原始文件；不合并来源；不下载模型权重；失败/需申请/禁商用记录原因不绕过。

---

## 一、已成功下载（原始文件，未改动）

| # | 文件 | 来源 | 大小(字节) | 授权 | 状态 |
|---|---|---|---|---|---|
| 1 | `data/CPsyCoun/CPsyCounD/Data/case_*.txt`（3,084 个） | GitHub CAS-SIAT-XinHai/CPsyCoun | ~12 MB | CC-BY-4.0 | ✅ 成功 |
| 2 | `data/CPsyCoun/CPsyCounE/`（45 用例，按主题分目录） | GitHub 同仓库 | ~184 KB | CC-BY-4.0 | ✅ 成功 |
| 3 | `data/CPsyCoun/README.md` `LICENSE` | GitHub 同仓库 | — | CC-BY-4.0 | ✅ 成功 |
| 4 | `data/CPsyCoun/CPsyCounD.json`（3,134 条） | hf-mirror `CAS-SIAT-XinHai/CPsyCoun` | 8,364,968 | CC-BY-SA-4.0（HF 标注） | ✅ 成功 |
| 5 | `data/CPsyCoun/README.hf.md` | hf-mirror 同仓库 | — | — | ✅ 成功 |
| 6 | `data/PsyDial/PsyDial-D101.json`（1,278 样例） | hf-mirror `qiuhuachuan/PsyDial-D101` | 4,187,349 | Apache-2.0 | ✅ 成功 |
| 7 | `data/PsyDial/PsyDial-D1.json`（2,382 对话） | hf-mirror `qiuhuachuan/PsyDial-D1` | 33,729,395 | Apache-2.0 | ✅ 成功 |
| 8 | `data/PsyDial/PsyDial-D4.json`（2,382 对话） | hf-mirror `qiuhuachuan/PsyDial-D4` | 35,544,509 | Apache-2.0 | ✅ 成功 |
| 9 | `data/PsyDial/README-D101.md` `README-D0_m.md` `README-D1.md` `README-D4.md` | hf-mirror | — | — | ✅ 成功 |

**已下载数据完整性校验**（JSON 均可解析）：CPsyCounD.json=3,134 条；PsyDial-D101=1,278；D1=2,382；D4=2,382。CPsyCounD/Data 共 3,084 个 txt（官方称 3,134，GitHub 版少 50 个——可能为 Data 目录与 json 的收纳差异，**已记录、未补全**）。

---

## 二、未下载 / 失败 / 需申请（记录原因，未绕过）

| 资源 | 状态 | 原因 |
|---|---|---|
| **CPsyCounR**（HF） | ⛔ 需申请授权 | gated=auto，须登录 HF 接受条件 + 分享联系方式，并签署《文本数据（案例）研究使用授权书》（**非商用、只读、有效期 2024-09-25~2027-07-01**）。未绕过。 |
| **PsyDial-D0_m**（HF） | ⛔ 需申请授权 | gated=manual，须 "Acknowledge license"（团队 2-3 天处理），并承诺不用于伤害人类受试实验。未绕过。 |
| **ESConv**（thu-coai GitHub） | ⛔ 禁商用，未下载 | LICENSE=**CC-BY-NC 4.0** + README "academic research use only"。Emora 为商业用途，**禁止纳入**，故不下载。仅记录供研究参考。 |
| **APA EBPP 指南 PDF** | ⛔ 下载失败 | apa.org Incapsula 反爬，curl 只返回 HTML 占位（212 字节）。可浏览器访问，不强绕反爬。已删除无效文件。 |

---

## 三、授权信息汇总（商用合规关键）

| 数据集 | 协议 | 允许商用？ | 附加限制 |
|---|---|---|---|
| CPsyCounD（GitHub） | CC-BY-4.0 | ✅ 署名即可 | 与 HF 的 CC-BY-SA-4.0 标注不一致，**从严按 SA + 署名**处理 |
| CPsyCounD（HF） | CC-BY-SA-4.0 | ✅ 署名+相同方式共享 | 同上 |
| CPsyCounR | 自定义授权书 | ⛔ 非商用 | 只读、不得二次传播、有效期、第三方须申请 |
| CPsyCounE | 同 CPsyCoun | ✅ | — |
| PsyDial D101/D1/D4 | Apache-2.0 | ✅ | 附带免责 |
| PsyDial D0_m | Apache-2.0（gated） | ✅（需申请） | 不用于伤害人类受试 |
| ESConv | CC-BY-NC 4.0 | ⛔ 禁商用 | 另有"academic use only"声明 |

---

## 四、镜像下载方法备忘（供后续复现）

```bash
# HuggingFace 直连不可达时，用镜像：
# API 查 gated/文件:
curl -s "https://hf-mirror.com/api/datasets/<org>/<repo>"
# 下载文件（resolve main）:
curl -sSL --http1.1 -o <out> "https://hf-mirror.com/datasets/<org>/<repo>/resolve/main/<file>"
# GitHub tarball（git clone 曾报 HTTP2 framing 错误，改用 tarball）:
curl -sSL --http1.1 -o repo.tar.gz "https://codeload.github.com/<org>/<repo>/tar.gz/refs/heads/main"
```

---

## 五、边界自检声明

- ✅ 未修改任何生产代码 / 提示词 / 数据库 / 运行环境。
- ✅ 未下载任何模型权重（CPsyCounX、PsyDial-Pi4 等模型均**仅记录、未下载**）。
- ✅ 未合并不同来源数据（各数据集分目录存放）。
- ✅ 未改动原始文件（仅原样复制/下载）。
- ✅ 未绕过任何 gated / 禁商用限制（CPsyCounR、PsyDial-D0_m、ESConv 均如实记录）。
- ⚠️ HuggingFace 直连失败已用镜像解决，镜像为社区公开服务，数据源仍指向官方 repo。