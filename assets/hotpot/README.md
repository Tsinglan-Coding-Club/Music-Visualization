# Hotpot 视觉素材

来源：`visualisation 游戏设计/visuals/` 中的用户提供草图插图（`img 1`–`img 8`）。

处理方式：使用 PIL 从四角对白／蓝底色做 flood fill，生成透明 PNG；`tomatoes.png` 额外去掉了原图自带的蓝色投影；所有食材再去掉贴边的白色抗锯齿像素，避免在深色背景上出现白边；裁掉透明边并保留 8px 内边距；最长边缩放到 520px；PNG 优化后保存。

| 文件 | 原图 | 用途 |
| --- | --- | --- |
| `pot.png` | `img 1.webp` | 左上 logo、底部主锅 |
| `bowl.png` | `img 2.jpg` | 顶部中央装饰 |
| `dish.png` | `img 3.avif` | 右下火锅盘 |
| `cabbage.png` | `img 4.webp` | 右上装饰 |
| `mushrooms.png` | `img 5.jpg` | 左下装饰 |
| `meat.png` | `img 6.jpg` | 左下肉盘 |
| `tomatoes.png` | `img 7.jpg` | 左上番茄 |
| `chopsticks.png` | `img 8.jpg` | 右下筷子 |
| `glow.png` | 程序生成 | 中央暖色径向光 |

插图在界面中的位置参考 `hotpot sketch 1/2/3` 的 img 编号；当前游戏只有一路判定，因此采用 sketch 1 的 Player A 2×4 卡座，B/C 玩家行留作后续多人扩展。

飞行食材直接绘制透明 PNG，本身不加圆盘或圆形描边；命中时从卡面上方弹出，受重力落下。
