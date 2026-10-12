# InnoWing 官方地图坐标与名称核对记录

核对日期：2026-10-12。数据版本：`official-axis-mm-2026-10-12-v1`。

## 坐标来源与换算

几何底图为官方会员页面的安全平面图：

- [G/F 1071 × 803](https://innowings.engg.hku.hk/wp-content/uploads/2020/09/fire-escape-route-GF.png)
- [LG/F 1096 × 782](https://innowings.engg.hku.hk/wp-content/uploads/2020/09/fire-escape-route-LGF.png)
- [官方会员页面](https://innowings.engg.hku.hk/innowing1/membership/)

轴网控制点为图上 DA–DI 与 D1–D6；每层分别读取轴线中心像素后独立拟合横、纵方向。原点为拟合得到的 DA/D6 交点，x 向右，y 向上；图上方不视为地理北。G/F 与 LG/F 使用同一套建筑轴网坐标，楼层另行保存，不推定楼层标高。

横向轴距链：2030、6750、7000、8000、7000、7000、6750、2030；合计 46560。纵向 D1→D6：2030、10750、6000、4750、2030；合计 25560。**D1→D2 是 2030，不能误读成 2300。**

使用者已接受按毫米解释上述数字，得到横向 46.56 m、纵向 25.56 m。现有栅格图没有找到明确的尺寸单位说明，故状态为 `accepted_mm_pending_verification`：这是依据图纸并经用户接受的毫米解释，尚待原始图纸单位或现场测量核验，不属于实测坐标。

换算采用：`xm = x.scale × pixel_x + x.offset`；`ym = y.scale × pixel_y + y.offset`。纵向系数必须为负值。参数、全部控制点、每个控制点的残差与拟合原点见 `map-calibration.json`。

| 楼层 | x 比例 m/px | y 比例 m/px | 合并轴网拟合 RMSE |
| --- | ---: | ---: | ---: |
| G/F | 0.05291694449 | -0.05290746350 | 0.00978 m |
| LG/F | 0.05292128677 | -0.05287942924 | 0.00626 m |

拟合残差描述所选栅格控制点与轴网标注的一致性，**不能作为现场测量精度**。读点误差约 1 px（约 0.053 m）；手工房间边界、原图墙厚、安全站黄色遮挡、布局年代差异带来的误差更大。LG/F 图上的 1:100 不能直接应用于已缩放的像素图。

地图距离属于名义平面直线距离。步行路径长度、通行宽度与物体计数需要其他证据，不能由这套边界自动推定。

## 房间名称与年代

名称主显示使用可明确对应的官方 **2023-08-30 活动地图**原文；保留 2020 路径底图名称作逐图对照：

- [2023 G/F 活动地图](https://innowings.engg.hku.hk/wp-content/uploads/2023/08/Slide1.jpg)
- [2023 LG/F 活动地图](https://innowings.engg.hku.hk/wp-content/uploads/2023/08/Slide2.jpg)
- [官方活动页面](https://innowings.engg.hku.hk/innowing1/enrichment2023/)

`official_name / label / name` 是主显示名称；`official_plan_name` 保存安全底图原文；`official_event_name_2023` 保存活动地图标签；`name_source` 和 `name_source_date` 记录来源。活动地图未能明确对应名称的空间保留安全图原名，并标为 `historical_official_raster_label`。所有名称均标记为官方图纸转录、尚未现场核验，不能声称是已确认的 2026 年现场名称。中英文文件夹名没有被自动当作房间身份。

主要可核对更新：

| 空间编号 | 安全底图原名 | 2023 官方显示名 |
| --- | --- | --- |
| G03 | CONFERENCE ROOM 1 | MEETING ROOM |
| G06 | CONFERENCE ROOM 2 | CONFERENCE ROOM |
| G07 | MULTI-PURPOSE AREA 1 | MAKERSPACE A |
| G08 | WOOD & METAL WORKSHOP | Machine shop |
| G10 | FUNCTION ROOM 1 | Thematic Workshop |
| G11 | LASER CUTTING | Laser cutting room |
| G15 | FUNCTION RM. 2 | Dreamchamber |
| G17 / G18 / G19 | MULTI-PURPOSE AREA 2 / 3D PRINTER / A.I. & ROBOTICS AREA | MAKERSPACE B |
| L01 / L08 | COMPUTER AID DESIGN LAB / BRAINSTORMING AREA | BRAINSTORMING AREA |
| L02 / L03 | TEACHING STUDIO 1 / TEACHING STUDIO 2 | Event Hall A&B |
| L04 / L05 / L06 / L07 | FUNCTION RM. 1 / FUNCTION RM. 2 / SPECIAL PROJECT ROOM 1 / SPECIAL PROJECT ROOM 2 | Project Studio（分别是不同物理房间） |
| L09 | SMALL MEETING ROOM 1 | Meeting Room B |
| L10 | SMALL MEETING ROOM 2 | Staff Office |
| L11 | MULTI-PURPOSE ROOM (STORE) | Store Room |
| L14 | SOUND PROOF RM. | Podcast Studio |
| L22 | AR VR MR STUDIO | VR Room |
| L23 | DIGITAL LEARNING LAB | Digital Learning Studio |

同一更新后大区的历史子区共享 `room_id / room_name`，统计时按 `room_id` 聚合，不能把子区算为多间真实房间。四个同名 Project Studio 有独立 room_id，因为图上仍是四个独立空间；不会虚构房间编号作为官方名称。

没有官方房间名的通行人工分段，主显示“通行区：…”且 `official_name = null`；没有名称的楼梯主显示描述名并保持 official_name 为 null。这些描述不是官方房名。

## 几何修正与待审核项

- 当前 61 个区域/历史子区来自人工描绘；`points` 为原图像素、`points_m` 为换算坐标，`label_m` 为标注位置，`centroid_m` 为多边形面积重心。面积重心未必位于凹多边形内部，显示标签优先使用 `label_m`。
- 修正 G15 Function Room 2 / Dreamchamber 下边界的右侧截断。
- G12 原来把普通与残障洗手间合并，已拆为 G12（LAV. 2）和 G31（DISABLED TOILET）。
- G23 原描绘落到楼梯内，已修正为安全图 SERVER RM. / 2023 ELV ROOM 所在上方及右侧小空间；单独补 G32（2023 ST1）楼梯。
- G26 与 L13 的部分墙线被黄色安全站图案遮挡，明确标为低边界置信度。
- G14、G20、L20、L21 等在 2023 图中没有可靠新标签，保留安全图历史原名，需现场核对。
- LG/F 北西部分隔墙在 2023 图中消失；L01/L08 同属 BRAINSTORMING AREA，L02/L03 同属 Event Hall A&B；GF G17/G18/G19 同属 MAKERSPACE B。旧多边形保留用于底图坐标对照，`is_historical_subregion` 与 `partition_status` 标明性质，不表示这些边界是现有隔墙。
- 所有多边形 `boundary_method = manual_raster_trace`，所有布局 `layout_review = needs_site_confirmation`。布局有更新时，应保存新版本并显式迁移标注，不能悄悄改变旧标注坐标。
