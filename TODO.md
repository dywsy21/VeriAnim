# TODO List

1. 继续跑更多run，检查会暴露出哪些rag和harness设计方面的问题
2. 完善ir设计
3. 跑benchmark
4. 穿模问题
5. 渲染质量问题
6. 论文
7. ppt
8. 材质完善检索
9. 代码整理成可复用的库
10. 项目打包优化。eg. uv tool install / curl什么玩意来安装，像codex一样输入codex可以直接启动cli；起个名字
11. 与ll3m解绑
12. 其他3D软件的适配。eg. unity, unreal, maya
13. 其他类型动画的适配。eg. 人体动作捕捉， cloth simulation, fluid simulation。分层extension？
  - RigidAnimationSpec：现在这套，继续强化。
  - CharacterAnimationSpec：骨架、mocap、IK、joint constraints。
  - DeformableSimulationSpec：cloth/soft body。
  - FluidSimulationSpec：fluid/smoke/particle。
  - SimulationCacheSpec：所有物理仿真共用。
  - VerificationProbeSpec：指定用 bbox、BVH、joint limits、particle/volume statistics、video verifier 哪种证据。
