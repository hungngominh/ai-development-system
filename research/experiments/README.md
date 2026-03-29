# Thí nghiệm

Thư mục này chứa các thí nghiệm tích hợp 5 thành phần.

## Template

Mỗi thí nghiệm tạo 1 file theo format:

```markdown
# <Tên thí nghiệm>

Ngày: YYYY-MM-DD
Người thực hiện: <tên>

## Mục tiêu
Mô tả ngắn gọn mục tiêu của thí nghiệm.

## Giả thuyết
Điều bạn mong đợi sẽ xảy ra.

## Cách thực hiện
Các bước cụ thể, lệnh chạy, config sử dụng.

## Kết quả
Output thực tế, screenshots, logs.

## Kết luận
So sánh kết quả với giả thuyết. Thành công hay thất bại? Tại sao?

## Bước tiếp theo
Cần làm gì tiếp? Thí nghiệm nào nên chạy tiếp?
```

## Ý tưởng thí nghiệm
1. Chạy CrewAI crew với agency-agents backstory — so sánh output với backstory generic
2. Tích hợp Beads tracking vào CrewAI execution loop
3. Dùng OpenSpec specs làm input cho CrewAI tasks
4. Test memory persistence qua nhiều sessions
5. Benchmark quality: có Superpowers vs không có
