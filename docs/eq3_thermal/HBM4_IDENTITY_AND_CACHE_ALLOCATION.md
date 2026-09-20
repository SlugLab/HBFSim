# HBM4身份与缓存分配核对

用户2026-09-20要求保持已定稿HBM4参数。本次未改configs/eq3_thermal/research/candidate_profile.json：Micron HBM4，每栈36GB标签、12DRAM die、2048bit、选定8Gbps/pin研究点、raw2.048TB/s。36GB精确字节口径原为UNKNOWN，保留。实际热normalized仍physical_type=HBM4、array_die_count=12；没有改成HBM2/3。HBM2时序/HBM3E功耗来源仅为单列代理，不改变目标器件。

4GiB/16GiB为四栈HBM中给缓存消融的**总软件分配空间**，不是每栈器件容量。填充/命中/替换由真实服务消费者处理。8HBF无封装HBM，不套用该缓存消融。

发现并局部修正未运行的因果输入生成器：它从HBF Grade2默认继承了HBM1.536TB/s媒体供给。新生成器显式读取原HBM4 profile的2.048TB/s raw上限，采用理想payload供给上界假设，实际效率仍未标定；并保留36GB/12H/2048bit身份。没有把厂商>2.8TB/s新数值替换原研究点。HBF16×96GB/s、几何、热网络、能量系数、温限不改。固定检查验证三个异构拓扑和4/16GiB缓存分配均消费同一HBM4声明。

旧raw及配置不覆盖：原BASE60/维护/ECC8无HBMarray/cache负载，HBM通道标签是未激活阵列供给；relay伙伴GPU链路原本已经2.048TB/s，其配额是否限制HBF需逐对报告，不能从无HBM前台结果推断混合流量表现。待执行缓存/因果实验必须使用新ID冻结且双方采用相同HBM4供给；任何旧HBM活跃性能结果不能直接沿用。

“按HBM4设计”不等于整个服务/能量模型已按HBM4实测标定；这项科学边界不通过重新命名消除。
