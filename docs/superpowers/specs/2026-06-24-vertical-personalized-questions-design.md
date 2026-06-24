# Spec 1 — Vertical-personalized debate questions

**Date:** 2026-06-24
**Status:** Design (approved in brainstorming, pending written-spec review)
**Related:** `2026-06-24-task-facet-taxonomy-design.md` (Spec 2 — builds on the `ProjectProfile` defined here)

---

## 1. Vấn đề

Khâu sinh câu hỏi debate hiện chỉ suy nghĩ theo **12 domain kỹ thuật** (backend, security,
database, infra, qa, product, …) và dàn agent debate cũng toàn vai kỹ thuật
(BackendArchitect, SecuritySpecialist, DatabaseSpecialist, ProductManager, DevOpsSpecialist,
QAEngineer). Prompt hỏi đúng một câu: *"quyết định kỹ thuật nào cần chốt để viết spec"*.

Hệ quả: **không có khái niệm "vertical / lĩnh vực sản phẩm"**. Một app cặp đôi không bao giờ
tự nảy ra câu hỏi về *tâm lý cặp đôi, thói quen dùng hằng ngày, động lực retention, an toàn
cảm xúc*; một app xe không tự hỏi về *hành vi mua của khách*. Đây là câu hỏi **sản phẩm/hành
vi**, không phải kỹ thuật — và hệ thống đang mù với chúng.

Bằng chứng trong code:
- Legacy generator: [`debate/questions/legacy.py:46-85`](../../../src/ai_dev_system/debate/questions/legacy.py) — prompt generic, không có ngữ cảnh vertical.
- v2 inventory: [`debate/questions/inventory.py:129-165`](../../../src/ai_dev_system/debate/questions/inventory.py) + `prompts/inventory.txt` — chỉ "atomic technical decisions".
- Domain registry: [`debate/domains.py:13-26`](../../../src/ai_dev_system/debate/domains.py) — 12 domain kỹ thuật, không có vertical/persona.
- Agent registry: [`debate/agents/registry.py`](../../../src/ai_dev_system/debate/agents/registry.py) — nạp persona từ `references/agency-agents/*.md`; toàn vai kỹ thuật.

## 2. Mục tiêu / Không-mục-tiêu

**Mục tiêu**
- AI **tự suy** một "vertical lens" từ ý tưởng/brief (không thêm bước thủ công cho người dùng).
- Câu hỏi debate phủ **cả** trục kỹ thuật **lẫn** trục sản phẩm/hành vi đặc thù vertical.
- Thêm **bộ persona phi-kỹ-thuật cố định** debate đúng góc nhìn (tâm lý, tăng trưởng/giữ chân, UX, thị trường).
- Hoạt động trên **cả nhánh legacy lẫn v2** (người dùng đang chạy legacy qua webui/`start`).

**Không-mục-tiêu (để Spec 2 / sau)**
- Task facet taxonomy (Input/Auth/Business rule/DB/Response/Error/NFR/Test ở mức task) → Spec 2.
- Persona động sinh theo dự án (đã loại trong brainstorming — dùng bộ cố định).
- Thêm bước xác nhận vertical cho người dùng (đã chọn "AI tự suy, không thêm bước").
- Sửa debate engine, Gate 1 UI, intake wizard.

## 3. Quyết định brainstorming (đã chốt)

1. **Độ sâu:** cả nội dung câu hỏi **và** chuyên gia mới.
2. **Nhận diện vertical:** AI tự suy, không thêm bước (người dùng vẫn duyệt ở Gate 1).
3. **Persona:** bộ cố định + lăng kính vertical (LLM điều chỉnh góc nhìn theo vertical).

## 4. Kiến trúc & thành phần

### 4.1 `ProjectProfile` — lăng kính (mới)

Module mới `src/ai_dev_system/debate/profile.py`:

```python
@dataclass
class ProjectProfile:
    vertical: str                  # nhãn ngắn, vd "app hẹn hò/cặp đôi", "marketplace xe cũ"
    primary_personas: list[str]    # người dùng thật, vd ["cặp đôi yêu xa"]
    key_dimensions: list[str]      # trục SẢN PHẨM/HÀNH VI quan trọng cho vertical này
    emotional_stakes: list[str]    # rủi ro/được-mất cảm xúc (rỗng nếu không liên quan)

    def is_empty(self) -> bool:    # True khi không suy được lens (vd stub LLM)
        return not self.key_dimensions


def infer_project_profile(brief, llm_client, *, config=None) -> ProjectProfile: ...
```

- 1 LLM call, prompt mới `debate/questions/prompts/profile.txt`: nhận idea (legacy) hoặc
  brief_v2/digest, trả JSON `ProjectProfile`.
- **Resilient:** JSON hỏng / LLM rỗng / stub → trả `ProjectProfile` rỗng (`key_dimensions=[]`).
  Profile rỗng = **không tiêm lăng kính** → hành vi y hệt hiện tại (giữ test cũ xanh).
- Persist `project_profile.json` thành artifact có version dưới
  `storage/runs/<run_id>/artifacts/project_profile/v1/`, tham chiếu bằng key
  `project_profile_id` trong `current_artifacts` (qua `RunRepo.update_current_artifact`).
  Mục đích: soi lại + Gate 1 hiển thị + **Spec 2 tái dùng**.

### 4.2 Bộ persona phi-kỹ-thuật cố định (mới)

Thêm 4 file `.md` vào `references/agency-agents/` đúng pattern hiện có (registry tự nạp,
**không sửa engine**):

| Agent key | Domain (mới) | typical_paired_with |
|---|---|---|
| `BehavioralPsychologist` | `psychology` | ProductManager, RetentionGrowthStrategist, UXResearcher |
| `RetentionGrowthStrategist` | `growth` | ProductManager, BehavioralPsychologist, MarketAnalyst |
| `UXResearcher` | `research` | ProductManager, BehavioralPsychologist, BackendArchitect |
| `MarketAnalyst` | `research` | ProductManager, RetentionGrowthStrategist |

Mỗi `.md` viết persona để **thích ứng theo vertical**: `BehavioralPsychologist` bàn "tâm lý cặp
đôi" cho app cặp đôi, "tâm lý người mua" cho app xe — góc nhìn lấy từ `ProjectProfile`.

### 4.3 Mở rộng domain registry

[`debate/domains.py`](../../../src/ai_dev_system/debate/domains.py): thêm 3 canonical domain sản
phẩm/hành vi — `psychology`, `growth`, `research` — kèm alias chuẩn hóa
(`behavior→psychology`, `emotion→psychology`, `retention→growth`, `churn→growth`,
`monetization→growth`, `ux→research`, `market→research`, `user-research→research`).
Cập nhật danh sách domain liệt kê trong `prompts/inventory.txt` cho khớp.

### 4.4 Tiêm lăng kính vào sinh câu hỏi

Thread `profile` (khi `not profile.is_empty()`) vào:

- **`prompts/inventory.txt`** (v2): thêm khối —
  *"Ngoài quyết định kỹ thuật, hãy liệt kê quyết định **sản phẩm/hành vi** theo các trục:
  `{key_dimensions}`. Chúng quan trọng ngang quyết định kỹ thuật. Gắn domain phù hợp
  (`psychology`/`growth`/`research`/`product`/`design`)."*
- **`prompts/materializer.txt`** (v2): truyền profile → diễn đạt câu hỏi theo vertical; với câu
  hỏi thuộc domain sản phẩm/hành vi, ưu tiên ghép `agent_a/agent_b` là persona mới.
- **`legacy.py`** (nhánh `my-love` đang dùng): chèn `vertical`, `key_dimensions` và 4 agent key
  mới vào cả `SYSTEM_PROMPT` và `SYSTEM_PROMPT_BRIEF_V2`, để legacy cũng sinh câu hỏi sản phẩm.

### 4.5 Ghép chuyên gia & coverage

- **Pairing:** persona mới có `domain` + `typical_paired_with` đầy đủ để
  [`registry.pair_suggestion`](../../../src/ai_dev_system/debate/agents/registry.py) ghép đúng
  (vd câu tâm lý cặp đôi → `BehavioralPsychologist` vs `RetentionGrowthStrategist`).
- **Coverage (mới, mức WARN):** thêm **C5** vào
  [`coverage.py`](../../../src/ai_dev_system/debate/questions/coverage.py): nếu `profile`
  không rỗng mà **không có** câu hỏi nào thuộc domain sản phẩm/hành vi (psychology/growth/
  research/product/design) → WARN "personalization có thể bị bỏ sót". Để WARN (không FAIL) để
  không phá run mà profile thật sự rỗng/thuần kỹ thuật.

### 4.6 Điểm cắm (path-agnostic)

Trong [`debate_pipeline.py` `_question_path` (68-103)](../../../src/ai_dev_system/debate_pipeline.py):
gọi `infer_project_profile(...)` **một lần** trước khi rẽ nhánh, rồi truyền `profile` vào **cả**
nhánh v2 (inventory→materializer→critic) **lẫn** nhánh legacy `generate_questions`. Vì cắm ở
ranh giới chung, người dùng thấy cải thiện ngay trên webui/`start`.

## 5. Luồng dữ liệu

```
idea / brief
    │
    ▼
infer_project_profile ──► ProjectProfile (artifact project_profile.json)
    │  (lăng kính, nếu không rỗng)
    ├─────────────────────────────┐
    ▼                             ▼
[v2] inventory(+lens)        [legacy] generate_questions(+lens)
    ▼
materializer(+lens, +personas mới)
    ▼
critic (giữ nguyên: chống SHALLOW)
    ▼
coverage (C1–C4 cũ + C5 WARN personalization)
    ▼
debate engine (KHÔNG đổi — nạp persona mới từ .md)
    ▼
Gate 1 — người dùng duyệt như cũ (profile hiển thị để tham khảo)
```

## 6. Tương thích ngược

- Personalization **cộng thêm**, không thay logic cũ. Profile rỗng ⇒ không tiêm ⇒ output
  câu hỏi không đổi.
- Test hiện có dùng `StubDebateLLMClient` ⇒ `infer_project_profile` trả profile rỗng ⇒
  golden questions/eval cũ **không đổi**.
- Persona mới chỉ là file `.md` + entry registry; agent cũ không bị ảnh hưởng.
- Cân nhắc feature flag `use_vertical_personalization` (mặc định bật) theo pattern
  [`feature_flags.py`](../../../src/ai_dev_system/feature_flags.py) để tắt nhanh nếu cần — quyết
  định ở bước writing-plans (mặc định: thêm flag, default on, vì repo dùng flag nhất quán).

## 7. Kiểm thử

**Unit**
- `infer_project_profile`: stub LLM hợp lệ → ProjectProfile đúng cấu trúc; JSON hỏng/rỗng →
  profile rỗng (resilient); persist + đọc lại artifact.
- `domains.py`: 3 domain mới + alias chuẩn hóa đúng.
- `registry`: 4 persona mới nạp được; `pair_suggestion` chọn chúng cho domain sản phẩm/hành vi.
- `legacy.py`: khi có profile, prompt chứa `key_dimensions` + agent key mới (assert nội dung
  prompt / output stub dùng chúng); khi profile rỗng, prompt y như cũ.
- `materializer`: profile được thread; câu hỏi thuộc dimension sản phẩm ghép persona mới.
- `coverage`: C5 WARN bật khi profile không rỗng nhưng thiếu câu hỏi sản phẩm; tắt khi đủ.
- `_question_path`: suy profile đúng một lần và truyền vào cả hai nhánh.

**Eval (golden dataset)**
- Thêm/mở rộng một golden idea cặp đôi: assert ≥ 3 câu hỏi (ngưỡng khởi điểm, tinh chỉnh ở
  writing-plans) thuộc domain sản phẩm/hành vi và có ít nhất một persona mới xuất hiện.
- Metric mới **`vertical_relevance`**: tỉ lệ câu hỏi gắn domain sản phẩm/hành vi hoặc tham chiếu
  `key_dimensions`. Đưa vào harness `ai-dev eval`.

## 8. Rủi ro & giảm thiểu

| Rủi ro | Giảm thiểu |
|---|---|
| AI suy nhầm vertical → câu hỏi lệch | Người dùng vẫn duyệt ở Gate 1; profile lưu artifact để soi; profile rỗng ⇒ fallback an toàn |
| Câu hỏi sản phẩm "mềm", khó debate | Critic giữ tiêu chí chống SHALLOW; persona mới viết stance rõ để debate có lực |
| Lạm phát số câu hỏi | Giữ trần số câu của inventory/coverage hiện có; dimension sản phẩm cạnh tranh chỗ với kỹ thuật, không cộng dồn vô hạn |
| Phá test golden cũ | Profile rỗng dưới stub ⇒ output cũ bất biến; thêm golden mới riêng cho vertical |

## 9. Ngoài phạm vi (chuyển Spec 2)

Task facet taxonomy: khi đơn vị là **task**, ép phủ đủ `input · auth_permission · business_rule ·
database · response · error_cases · non_functional · test_cases`, dùng chung `ProjectProfile`
làm lăng kính, cắm vào cả luồng project→task lẫn chế độ nhập task lẻ. Xem
`2026-06-24-task-facet-taxonomy-design.md`.

## 10. Tệp dự kiến đụng tới

**Mới**
- `src/ai_dev_system/debate/profile.py`
- `src/ai_dev_system/debate/questions/prompts/profile.txt`
- `references/agency-agents/behavioral-psychologist.md` (+ 3 persona còn lại)
- tests tương ứng dưới `tests/unit/...`

**Sửa**
- `src/ai_dev_system/debate/domains.py` (3 domain + alias)
- `src/ai_dev_system/debate/questions/inventory.py` + `prompts/inventory.txt`
- `src/ai_dev_system/debate/questions/materializer.py` + `prompts/materializer.txt`
- `src/ai_dev_system/debate/questions/legacy.py`
- `src/ai_dev_system/debate/questions/coverage.py` (C5)
- `src/ai_dev_system/debate_pipeline.py` (`_question_path` cắm profile)
- `src/ai_dev_system/db/repos/runs.py` nếu cần key artifact mới (hoặc dùng `update_current_artifact`)
- eval harness (golden idea + metric `vertical_relevance`)
- `src/ai_dev_system/feature_flags.py` (nếu thêm flag)
```
