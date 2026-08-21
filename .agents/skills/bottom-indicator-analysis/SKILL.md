---
name: bottom-indicator-analysis
description: >
  Hệ thống Định Lượng Chỉ Báo Đáy & Nhiệt Kế Thị Trường (Market Emotion & Smart Money Analysis).
  Áp dụng cho mọi yêu cầu phát triển, bảo trì, phân tích kỹ thuật dòng tiền tạo lập (Bigboys),
  nhận diện vùng tạo đáy xác thực, chống bắt dao rơi, phân kỳ đa chỉ báo (RSI + Smart Money + MACD),
  và bộ định lượng Price-Volume Wyckoff (Stopping Volume, Absorption, Climax, News Distribution vs SOS).
---

# Hướng Dẫn Vận Hành Hệ Thống Chỉ Báo Đáy & Dòng Tiền Tạo Lập (Smart EMA-Volume-Wyckoff Engine)

Skill này đóng gói toàn bộ quy chuẩn định lượng và triết lý giao dịch của hệ thống Lộc Phát Securities.

## 1. Bản Chất 3 Biểu Đồ Cốt Lõi
1. **Nến giá · EMA 20/50/100/200:** Hành động giá thực tế (Price Action), hình học nến (CLV, Wick Ratios, Spread/ATR), nhận diện cấu trúc Wyckoff (Spring/SOS/LPS), điểm chuyển pha, điểm mua gom **BB** và điểm bán đỉnh **BS**.
2. **Smart Money Start:** Hành vi của **Bigboys / Dòng tiền tạo lập ("Cáo")** qua 3 dải băng:
   - `Pulse` (EMA 5): Xung lực tiền lớn ngắn hạn.
   - `Flow` (EMA 13): Xu hướng dòng tiền tạo lập trung hạn.
   - `Core` (EMA 34): Vị thế dòng tiền lõi dài hạn.
3. **Market Emotion Index:** Nhiệt kế cảm xúc của **Đám đông thị trường ("Gà")** từ 0 (Hoảng loạn/Call margin) đến 100 (FOMO cực độ).

## 2. Các Quy Tắc Bất Biến & Bộ Nhận Diện Volume–Wyckoff
1. **Triết lý Cáo vs Gà:** Không bao giờ xem Bigboys và Đám đông cùng suy nghĩ. Khai thác `Disparity = Bigboys - Emotion` để tìm cơ hội khi đám đông hoảng loạn và phòng thủ khi đám đông hưng phấn cực độ.
2. **Bối cảnh Khối Lượng (Volume Context):**
   - **Vùng Đáy (Bottom Volume):**
     - `STOPPING_VOLUME`: Giảm dài $\rightarrow$ Volume lớn $\rightarrow$ Nến rút chân râu dưới dài ($\text{Lower Wick} \ge 28\%$, $\text{CLV} \ge 0.18$) $\rightarrow$ Lực bán bị chặn đứng.
     - `HIGH_VOLUME_ABSORPTION`: Nỗ lực (Volume) cực lớn ($\text{RVOL} \ge 1.65$), $\text{Effort vs Result} \ge 1.18$ (khối lượng khổng lồ nhưng biên độ rơi bị nén chặt), thể hiện Bigboys nuốt trọn lượng cung cắt lỗ.
     - `LOW_VOLUME_SPRING` / `SUPPLY_DRYUP`: Quét thủng đáy nhưng rút chân đóng trên đáy với Volume co hẹp ($\text{RVOL} \le 0.90$), cạn kiệt cung bán.
     - `THREE_BAR_REVERSAL`: Nến A đỏ mạnh $\rightarrow$ Nến B rút chân/stopping volume $\rightarrow$ Nến C xanh xác nhận với $\text{Pulse} > \text{Flow}$.
     - `VOLUME_DRYUP_DIVERGENCE`: Giá tạo đáy mới thấp hơn nhưng Volume tại đáy mới giảm $\ge 20\%$ so với đáy cũ.
     - `CAPITULATION_ABSORBED`: Phiên bán tháo hoảng loạn kỷ lục ($\text{RVOL} \ge 2.0$, $\text{Aperture} \le 35$) được hấp thụ sạch.
   - **Vùng Đỉnh & Phân Phối (Top Volume):**
     - `BUYING_CLIMAX`: Tăng dài $\ge 25-35\%$ $\rightarrow$ Volume cực đại $\rightarrow$ Bị bán rụt đầu ($\text{Upper Wick} \ge 35\%$ hoặc $\text{CLV} \le -0.15$).
     - `UPTHRUST` (UTAD): Vượt đỉnh trong phiên nhưng bị bán dội ngược đóng cửa sát đáy nến ($\text{CLV} \le -0.25$) với volume lớn.
     - `EFFORT_VS_RESULT_DISTRIBUTION`: Khối lượng tăng vọt ở đỉnh nhưng giá đứng im hoặc nến đỏ thân hẹp.
     - `NEWS_EUPHORIA_DISTRIBUTION_RISK`: Đã tăng dài + Gần đỉnh/kháng cự + Tin tức truyền thông hưng phấn + $\text{RVOL} \ge 1.5$ + Upper wick / poor close + $\text{Pulse} < \text{Flow}$ $\rightarrow$ Nhận diện bẫy xả hàng tin tốt.
     - `NEWS_SOS` (Anti-False-Distribution): Tin tốt + $\text{RVOL} \ge 1.5$ + Breakout cản dứt khoát + $\text{CLV} \ge 0.55$ + Râu trên nhỏ + $\text{Pulse} > \text{Flow}$ $\rightarrow$ Xác nhận dòng tiền lớn đẩy giá (SOS), cấm phát BS!
3. **Anti-Bottom-Sell:** Cấm phát tín hiệu BS khi cổ phiếu đang trong vùng quá bán sâu / hấp thụ rũ bỏ ($\text{Aperture} \le 40, \text{RSI} \le 42, \text{Dist to Low} \le 10\%$, hoặc đang xuất hiện mẫu hình Stopping Volume / Absorption / Spring).
4. **Anti-Falling-Knife:** Cấm phát tín hiệu Đáy hoặc BB khi đường $\text{EMA20}$ đang cắm dốc rơi tự do ($\text{EMA20}_{slope} \le -1.2\%$) mà không có phân kỳ tạo đáy.
5. **Đa Phân Kỳ (Multi-Divergence):** Hỗ trợ nhận diện Phân kỳ Đơn (Single), Kép (Dual), và Tam phân kỳ (Triple: RSI + Smart Money + MACD Histogram) để lọc nhiễu tối đa.
