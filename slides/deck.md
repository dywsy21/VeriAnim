<!-- .slide: class="title-slide" -->

:::{kicker}
Group 1 / Wang Siyu, Du Yuheng, Yang Tingyi, Tong Mingyang
:::

# VeriAnim: Animation Extension Contracts for Verifiable Blender Programs

:::{subtitle}
从自然语言到自我迭代的 Blender 动画生成 Agent
:::

???
- 这页只讲一句话：我们不是直接让模型写关键帧，而是让动画先变成可检查、可修复的合同。

---

# 需要解决的问题

:::{split-emphasis}
静态 3D 生成只需要“场景像不像”；动画还要回答“谁（物体、光源、甚至摄像机）在什么时候动、是否符合物理规律、镜头是否真的看见了”。
:::

- 直接生成 Blender keyframes 很容易只满足起点和终点的正确性。
- 中间帧可能穿模、漏掉夹爪、把相机转到看不见动作的位置。
- 这些问题的原因在于：约束往往并不能显示地被用户需求所表达，它是进一步分析用户需求后得出的。

---

# 我们的目标

[columns]
[column]
:::{card}
**输入**
- 一段自然语言 prompt
- 目标是可执行、可编辑的 Blender 程序
- 支持静态场景 + 动画
:::
[/column]

[column]
:::{card}
**输出**
- Blender 可运行的 Python script
- scene graph / transform traces
- 截图 / 预览视频
- 自动验证报告 / 修正记录
:::
[/column]
[/columns]

:::{callout}
显然不是“生成一个视频”，而是生成一个能被检查、定位问题、局部修复的动画描述程序。
:::

---

# 核心想法：两层 IR

[columns]
[column]
:::{card}
**1. SceneSpec IR**
- 静态场景作为基础
- 各个对象的 ID / 有自由度的独立部位 / 材质
- 对象之间关系 / 摄像机 / 碰撞 proxy
- 计划怎样验证？应该满足怎样的约束？
:::
[/column]

[column]
:::{card}
**2. Animation Extension Contract**
- 在静态基线上添加时间
- event windows / animation families
- verifier probes / capability profiles
- media artifacts / repair scope
:::
[/column]
[/columns]

:::{tag-row}
`stable ids` `event windows` `probes` `audit trail`
:::

---

# 系统流程

:::{pipeline}
:::{card}
**1. Plan**
prompt -> SceneSpec IR + animation extension
:::

:::{card}
**2. Build static scene**
生成对象、材质、灯光、相机，并先验证静态场景
:::

:::{card}
**3. Add animation**
引用 object ids，写 keyframes / helpers / media sampling
:::

:::{card}
**4. Verify + repair**
几何检查 + 视觉/视频检查 + bounded local repair
:::
:::

:::{callout}
先构建静态场景，验证通过后，动画阶段引用静态场景中的物体加关键帧；这样在修复动画问题时确保避免改动物体模型本身。
:::

---

# SceneSpec IR 做什么

- 把 prompt 中的对象、部件、材质、空间关系、相机和证据计划显式化。
- 每个对象有稳定 `verianim_id`，后续代码、采样、验证和修复都用同一套 id。
- 空间关系不仅说“杯子在桌上”，还要说用什么方式检查 support / containment / attachment。
- 碰撞 proxy 让几何验证可以在 Blender 执行后测量，而不是只相信模型描述。

:::{metric}
**直接收益**
动画生成从“重写完整场景”变成“在已验证场景上添加可审计的时间事件”。
:::

---

# Animation Extension 做什么

[columns]
[column]
:::{card}
**Families**
- rigid motion
- camera motion
- visibility/state change
- deformable events
- mixed scenes
:::
[/column]

[column]
:::{card}
**Evidence**
- bbox / BVH / contact probes
- camera coverage reports
- screenshots and video
- deformation statistics
:::
[/column]

[column]
:::{card}
**Runtime honesty**
- supported
- degraded
- unsupported scope
:::
[/column]
[/columns]

:::{callout}
原则：每一种动画能力都必须说明它靠什么证据被验证。
:::

---

# 目前最成熟：刚体 primitive

- **support motion**：滑动、落到支撑面、沿支撑物移动。
- **paired interaction**：推、携带、保持 driver-payload offset。
- **pick-place**：approach, grasp, lift, transfer, lower, release。
- **articulation**：门轴、转子、绕有意义的 pivot 转动。
- **composition**：每个 frame window 只有一个 transform owner，避免互相抢控制权。

:::{warning}
这些 primitive 不是为了写“更漂亮”的 keyframes，而是为了让每段动作都有可验证的不变量。
:::

---

# 验证与修复

[columns]
[column]
:::{card}
**Deterministic checks**
- support gap and overlap
- non-penetration
- containment
- transform ownership
- dense frame audit
:::
[/column]

[column]
:::{card}
**Media checks**
- subject visibility
- camera framing
- temporal order
- apparent final state
- video verifier feedback
:::
[/column]
[/columns]

:::{metric}
**修复策略**
用测量证据改局部 motion path：例如重新计算物体底部和支撑面顶部的高度，而不是要求模型整段重写。
:::

---

# 我们已经实现了什么

- Blender addon socket server：执行脚本、检查场景、渲染截图和 preview video。
- Python harness：planner / coder / refiner / visual verifier / video verifier。
- IR parser and serializer：静态 SceneSpec + animation extension。
- Deterministic validator：几何关系、transform traces、frame-window auditing。
- Texture resolver：允许 Agent 联网搜索 texture。
- Harness KV Cache Optimization：多轮 refine 情况下的 KV Cache 命中优化
- VeriAnim-AnimBench：300 条 prompt，easy / medium / hard 各 100。

---

# 遇到的问题

[columns]
[column]
:::{warning}
**穿模仍然最常见**
- 运动过程中间物体穿过另一物体
- 使用 bbox 判定对凹形物体不够精确
- sparse samples 容易漏掉短暂接触错误
:::
[/column]

[column]
:::{warning}
**模型能力差导致难以自我更正**
- 反复 refinement 仍然改不到失败原因
- 破坏那些好的、已有的东西
- 会“解释”错误，而不是修复
:::
[/column]
[/columns]

:::{callout}
这也是我们强调 bounded repair 和 deterministic evidence 的原因：不能只把失败报告丢回给模型。
:::

---

# Showcase：可验证动画样例

[columns]
[column]
:::{showcase-card}
![Collision two cubes](../showcase/collision_two_cubes/animation.gif){.showcase-gif}
**collision_two_cubes**

- Prompt: `A physical experiment: a moving square hits a stationary square. The same mass, no energy loss, no friction.`
- Result: blue cube stops at contact; red cube departs rightward.
- Evidence: deterministic + scene vision + animation video all passed.
:::
[/column]
[column]
:::{showcase-card}
![Marble run into cup](../showcase/marble_run_into_cup/animation.gif){.showcase-gif}
**marble_run_into_cup**

- Prompt: `marble rolls down a supported ramp and stops inside a blue catch cup.`
- Result: ramp/support/cup stay fixed; marble motion and final containment are visible.
- Evidence: scene preservation + deterministic + animation video all passed.
:::
[/column]
[/columns]

:::{muted}
两个例子都保留了生成源码、`.blend`、GIF、prompt、模型 manifest 和 verifier 摘要，便于现场打开追溯。
:::

---

# Benchmark：refine 轮数作为大模型评测指标

:::{metric}
**观察**

我们发现不同模型的能力差异体现在动画制作上的效果是显著的。可以通过 Refine 轮数去对不同模型的能力进行评测。
:::

- `0 rounds`：一次生成就满足几何和媒体证据。
- `1-2 rounds`：局部修复有效，说明合同和证据足够定位问题。
- `many rounds / stop`：通常对应弱模型、欠约束 primitive、或 verifier 无法给出可操作证据。
- 我们正在把 refine rounds 纳入 AnimBench 评测。

---

# 仍存在的问题与下一步

- 更精细的 mesh/BVH 检查，减少 bbox 对穿模的误判和漏判。
- 让 camera / visibility / deformable family 的 primitive 和验证更完整。
- 把“弱模型无法更正”的失败类型系统化，区分模型能力、证据质量和合同缺失。
- 用 refine rounds 作为 benchmark 指标，衡量模型能力。
