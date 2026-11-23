# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV，除采纳与隔离行外还包含每份到期授权的休眠证明或待核凭证行，kind列为结论，与复核页面、分区复算读取同一批次结果。休眠判定参数：grants、as_of（缺省取最晚授权到期时刻）、observe_months（正整数或full，默认1）。
