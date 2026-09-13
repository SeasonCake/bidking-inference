# BidKing：0.3.5-hotfix1 产品状态

[English](DEVELOPMENT_STATUS.md) · [返回首页](../README.md)

记录日期：2026-09-13。当前产品热修与公开 Python 库分别维护；库仍为 **v0.1.0**。
本次只更新源码研究与带日期的产品信息，不创建新的 GitHub 产品 Release 或替换旧资产。

## 本次热修

0.3.5-hotfix1 已完成选定的本地成品验收与代表性实机流程，维护者另确认实际使用正常。
更新包括游戏 b049 适配、新增地图算法报价与完整品质件格信息、残骸经验估值，以及启动实例、
退出所有权和部分估价提示的修正。代表场景完成同局截图、导出和正常退出关联；这不等于全地图、
全英雄和全部机器环境覆盖。

本轮服务器与版本提示配置保持原状态。原先后移的换绑/服务配套、更多类型观测与尚未选定功能，
不能因为客户端热修完成就写成已上线。没有公布产品准确率或普适提速百分比。

## 下载与验证范围

- 已有 [GitHub 0.3.5 产品发行](https://github.com/SeasonCake/bidking-inference/releases/tag/product-v0.3.5)
  及旧发行保留；其原 ZIP 曾完成完整下载与 SHA 核对。
- 维护者提供 [0.3.5-hotfix1 下载](https://bidking-dist-1317950063.cos.ap-shanghai.myqcloud.com/bidking-live-v0.3.5-hotfix1-encrypted.zip)。
  本地验收包为 95,883,037 字节，SHA-256：
  `a3f0553e40f24ba2cccc85860964a6ee6359ebf810a6487fb4a2f7117aa8829f`。
- 2026-09-13独立完整下载返回200，取得全部95,883,037字节，SHA与上述本地验收包一致。
  这是新对象自己的读回结果；没有创建新的GitHub产品发行或重新上传资产。

## 现在可以运行的研究

本次把成熟工程问题整理为独立合成例子，读者不需要游戏、账号或产品输入：

- [后验、评估与生命周期](research/POSTERIOR_AND_LIFECYCLE.zh-CN.md)：完整边际与截断尾部，
  train/holdout，聚合抵消与逐例误差，stop/finished/reaped和成员闭合。
- [自有Windows实验](research/NATIVE_LAB.zh-CN.md)：构建/运行实例、ABI、PDB、MVID、局部符号栈，
  编码/typed事件的冷进程与暖态pipe对照、慢消费者和epoch重连。
- [已有观测资格](EVIDENCE_LIFECYCLE.zh-CN.md)与[故障工作流](FAILURE_WORKFLOWS.zh-CN.md)继续复用。

稳定库13模块、原56份历史源码、7份旧数据和既有图片/发行保持。当前产品模型、真实数据、
服务、采集、激活及生产实现不进入本次公开增量。见[项目关系](../PROJECT_RELATIONSHIP.md)、
[开源范围](../OPEN_SOURCE_BOUNDARY.md)与[研究路线](research/ROADMAP.zh-CN.md)。
