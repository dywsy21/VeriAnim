<!-- .slide: class="title-slide" -->

:::{kicker}
Class Report / 10 minutes
:::

# VeriAnim: Animation Extension Contracts for Verifiable Blender Programs

:::{subtitle}
从自然语言到可验证的 Blender 动画：我们做了什么、怎么做、失败在哪里、接下来如何评测
:::

???
- 这页只讲一句话：我们不是直接让模型写关键帧，而是让动画先变成可检查、可修复的合同。

---

# 一句话问题

:::{split-emphasis}
静态 3D 生成只需要“场景像不像”；动画还要回答“谁在什么时候动、关系是否一直成立、镜头是否真的看见了”。
:::

- 直接生成 Blender keyframes 很容易只满足起点和终点。
- 中间帧可能穿过桌面、漏掉夹爪、把相机转到看不见动作的位置。
- 这些失败不是 Blender 不适合，而是动画缺少结构化合同和证据。

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
- Blender Python script
- scene graph / transform traces
- screenshots / preview video
- verifier reports / repair history
:::
[/column]
[/columns]

:::{callout}
核心不是“生成一个视频”，而是生成一个能被检查、定位问题、局部修复的动画程序。
:::

---

# 核心想法：两层合同

[columns]
[column]
:::{card}
**1. SceneSpec**
- 冻结静态场景基线
- object ids / parts / materials
- relations / cameras / collision proxies
- screenshot and evidence plan
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
prompt -> SceneSpec + animation extension
:::

:::{card}
**2. Build static scene**
生成对象、材质、灯光、相机，并先验证静态场景
:::

:::{card}
**3. Add animation**
引用稳定 object ids，写 keyframes / helpers / media sampling
:::

:::{card}
**4. Verify + repair**
几何检查 + 视觉/视频检查 + bounded local repair
:::
:::

:::{callout}
静态场景先通过，动画阶段只能引用它；这样修复穿模时不会顺手把桌子、相机或材质重生成。
:::

---

# SceneSpec 做什么

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
- Texture resolver：把材质检索和视觉审批变成可追踪 preprocessing。
- VeriAnim-AnimBench：300 条 prompt，easy / medium / hard 各 100。

---

# 遇到的问题

[columns]
[column]
:::{warning}
**穿模仍然最常见**
- 中间帧穿过支撑面
- bbox 对凹形物体不够精确
- sparse samples 容易漏掉短暂接触错误
:::
[/column]

[column]
:::{warning}
**弱模型很难自我更正**
- 反复 refinement 仍然改不到失败原因
- 有时会破坏静态场景基线
- 会“解释”错误，而不是修 motion path
:::
[/column]
[/columns]

:::{callout}
这也是我们强调 bounded repair 和 deterministic evidence 的原因：不能只把失败报告丢回给模型。
:::

---

# Showcase 占位

[columns]
[column]
[[placeholder: GIF 1 | prompt=Prompt: TBD]]
[/column]
[column]
[[placeholder: GIF 2 | prompt=Prompt: TBD]]
[/column]
[/columns]

:::{muted}
之后这里会放若干成功/失败 showcase：GIF + 对应 prompt + verifier 或 repair 简短结果。
:::

---

# Benchmark：refine 轮数作为指标

:::{metric}
**观察**
动画 prompt 的难度不只体现在最终 pass/fail，也体现在系统需要多少轮 refinement 才能定位并修复局部失败。
:::

- `0 rounds`：一次生成就满足几何和媒体证据。
- `1-2 rounds`：局部修复有效，说明合同和证据足够定位问题。
- `many rounds / stop`：通常对应弱模型、欠约束 primitive、或 verifier 无法给出可操作证据。
- 我们正在把 refine rounds、失败签名重复次数和 wall-clock cost 纳入 AnimBench 评测。

---

# 仍存在的问题与下一步

- 更精细的 mesh/BVH 检查，减少 bbox 对穿模的误判和漏判。
- 让 camera / visibility / deformable family 的 primitive 和验证更完整。
- 把“弱模型无法更正”的失败类型系统化，区分模型能力、证据质量和合同缺失。
- 完成 showcase GIF 与 prompt 对照，展示成功、失败和 repair 的全过程。
- 用 refine rounds 作为 benchmark 指标，衡量动画生成的可修复性而不仅是最终结果。

:::{callout}
结论：VeriAnim 把动画生成从一次性 keyframe 写作，变成带合同、证据和局部修复的程序合成流程。
:::
