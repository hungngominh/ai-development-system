# Phan tich tri nho AI: 4 tang va giai phap

## Van de

AI (LLM) co gioi han tri nho nghiem trong. Moi model chi co the "nho" trong pham vi context window cua no, va khi het phien lam viec thi mat sach. Day la 4 tang van de tri nho can giai quyet:

1. **Context window day** — Cuoc hoi thoai dai, AI quen phan dau. Khi so luong token vuot qua gioi han (128K-1M tokens tuy model), nhung thong tin dau tien bi day ra ngoai va mat vinh vien trong phien do.
2. **Het session** — Phien moi = nao trang. AI khong nho bat ky dieu gi tu phien truoc, ke ca nhung quyet dinh quan trong hay context du an.
3. **Giua cac agent** — Agent A khong biet Agent B da lam gi. Trong he thong multi-agent, moi agent hoat dong trong "bong toi" ve nhung gi agent khac da thuc hien.
4. **Dai han** — AI quen du an sau vai ngay/tuan. Khong co co che nao de AI tu dong nho lai nhung gi da xay ra truoc do.

## Bang danh gia

| Tang | Van de | Giai phap | Repo | Muc do |
|------|--------|-----------|------|--------|
| 1. Context window | Hoi thoai dai quen dau | Beads compaction (tom tat issue cu) | Beads | :warning: Mot phan |
| 2. Cross-session | Phien moi = trang | Persistent DB + specs on disk + vector memory | Beads + OpenSpec + CrewAI | :white_check_mark: Tot |
| 3. Cross-agent | Agent A != biet Agent B | Scoped memory + dependency graph | CrewAI Memory + Beads | :white_check_mark: Tot |
| 4. Dai han | Quen sau tuan/thang | Version-controlled DB + vector store + files | Dolt + LanceDB + specs | :white_check_mark: Tot |

## Chi tiet tung tang

### Tang 1: Context Window (:warning: Mot phan)

- **Gioi han vat ly**: moi LLM chi xu ly ~128K-1M tokens. Day la hard limit cua kien truc transformer.
- **Beads compaction**: tom tat issue cu bang AI (Tier 1 summarization), giam kich thuoc context. Nhung issue da resolve duoc nen thanh ban tom tat ngan gon.
- **OpenSpec**: thay vi nho, AI doc spec file khi can. Spec file hoat dong nhu "bo nho ngoai" — AI khong can giu trong context ma chi doc khi lien quan.
- **CrewAI Memory recall flow**: chi lay memory lien quan nhat (semantic search + importance scoring). Khong load toan bo memory ma chi nhung gi relevant voi task hien tai.
- **Van chua triet de**: task qua phuc tap trong 1 session van co the quen. Neu mot cuoc hoi thoai can xu ly nhieu file phuc tap cung luc, context window van bi day.

### Tang 2: Cross-session (:white_check_mark: Tot)

- **Beads**: Dolt DB persistent, moi task state luu vinh vien. Khi bat dau phien moi, AI co the query lai toan bo trang thai tu database.
- **OpenSpec**: specs tren disk, archive co timestamp. Moi phien ban spec duoc luu voi thoi gian, cho phep xem lai bat ky thoi diem nao.
- **CrewAI Memory**: LanceDB vector store, persist giua sessions. Memory duoc luu duoi dang vector embeddings, cho phep tim kiem theo ngu nghia.
- **Phien moi**: AI doc lai tu 3 nguon persistent storage — database (Dolt), files (OpenSpec), va vector store (LanceDB).

### Tang 3: Cross-agent (:white_check_mark: Tot)

- **CrewAI Memory scope phan cap**: `/crew/project/agent-name`. Moi agent co memory rieng, nhung cung co the truy cap shared scope.
- **Agents chia se memory** qua shared scope. Khi Agent A hoan thanh task, ket qua duoc ghi vao shared memory de Agent B co the doc.
- **Composite scoring**: `semantic_weight(0.5) + recency_weight(0.3) + importance_weight(0.2)`. Memory duoc xep hang theo do lien quan, moi gan day, va muc do quan trong.
- **Consolidation**: tu gop memory trung (threshold 0.85). Khi hai memory co do tuong dong > 85%, chung duoc merge lai thanh mot de giam nhieu.
- **Beads**: Agent A close task -> Agent B chay `bd ready` -> thay task moi. Day la co che handoff co cau truc giua cac agent.

### Tang 4: Dai han (:white_check_mark: Tot)

- **Dolt**: Git cho database, version history vinh vien. Su dung `--as-of` de xem trang thai database tai bat ky thoi diem nao trong qua khu.
- **LanceDB**: vector store persistent, memory decay configurable (half-life 30 ngay). Memory cu dan mat do quan trong theo thoi gian, nhung khong bi xoa hoan toan.
- **OpenSpec**: file specs + archive history tren disk. Moi thay doi spec duoc luu lai, tao thanh lich su phat trien cua du an.

## 3 lo hong con lai

### 1. Intra-session

Context window day giua cuoc hoi thoai. Day la van de kho nhat vi khong the them memory tu ben ngoai khi dang trong mot phien.

**Workaround**: Superpowers writing-plans tao plan file tren disk, AI doc lai khi can. Thay vi giu toan bo plan trong context, AI ghi plan ra file va chi doc lai phan can thiet.

### 2. Handoff quality

Output giua agents co the mat implicit knowledge. Khi Agent A truyen ket qua cho Agent B, nhung hieu biet "ngam" (implicit) co the bi mat — chi co explicit output duoc truyen di.

**Workaround**: Beads structured summary dam bao output co cau truc, giam thieu viec mat thong tin quan trong.

### 3. Memory accuracy

AI khong tu validate memory cu. Memory co the bi outdated hoac sai nhung AI van tin tuong va su dung.

**Workaround**: Beads event trail + OpenSpec validation cross-check. Moi memory co the duoc kiem tra cheo voi event log va spec files de dam bao do chinh xac.

## Danh gia tong

**Score: 7/10**

Tot hon 95% setup AI hien tai (hau het khong co persistent memory nao). He thong nay giai quyet duoc 3/4 tang van de mot cach tot, va tang con lai (context window) co workaround chap nhan duoc.

**Diem manh**:
- Persistent storage da tang (database + files + vector store)
- Cross-agent communication co cau truc
- Version history cho moi thay doi

**Diem yeu**:
- Intra-session memory van phu thuoc vao context window size
- Handoff giua agents chua hoan hao
- Chua co tu dong validation cho memory cu

## Huong nghien cuu tiep

- Cai thien intra-session qua checkpoint mechanisms — luu trang thai giua cuoc hoi thoai
- Structured handoff formats giua agents — dinh dang chuan cho viec truyen thong tin
- Memory validation pipeline tu dong — tu dong kiem tra do chinh xac cua memory
- Xem them: [docs/diagrams/memory-layers.md](diagrams/memory-layers.md) cho so do truc quan
