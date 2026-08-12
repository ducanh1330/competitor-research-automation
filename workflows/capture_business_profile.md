# Capture Business Profile

## Objective
Interview the user about their business and write `profile/company.md` (prose, user-editable)
and `profile/company.json` (structured, tool-readable). This profile is what makes competitor
discovery accurate and the comparison specific rather than generic — every downstream
workflow reads it.

## Inputs
| Input | Required | Notes |
|---|---|---|
| A conversation with the user | yes | ~15 minutes. Ask; do not infer. |
| Company website | no | Useful for pre-filling, but confirm every inference |
| Existing sales/marketing material | no | Good source for positioning language |

## Steps

1. **Pre-fill what is public, if a website is available.** Snapshot the user's own
   homepage and pricing page with `fetch_page_snapshot.py` under the competitor slug
   `self`. Draft answers from it, then bring the draft to the interview as something
   to *correct* rather than questions to answer from scratch — this is faster and
   more accurate than an open-ended interview.

2. **Interview across the four agreed areas.** Ask these; do not substitute assumptions.

   **A. Identity & offering**
   - What do you sell, in one sentence a customer would recognise?
   - What are the actual products / services / SKUs?
   - What category do you compete in — what would a buyer type into a search box?
   - What do you believe makes you different?

   **B. Customers & market**
   - Who is the ideal customer? Company size, role, industry.
   - Which geographies do you serve?
   - Any verticals you specialise in or deliberately avoid?
   - What problem is the customer trying to solve when they find you?

   **C. Pricing & business model**
   - What are your price points and what does each include?
   - Model: subscription, retainer, project, per-unit, usage?
   - Typical deal size and contract length?
   - Do you discount, and under what circumstances?

   **D. Go-to-market & channels**
   - How do customers currently find you?
   - Which marketing channels are active? Which have you tried and dropped?
   - What is the sales motion — self-serve, sales-led, partner-led?
   - Where do you publish (blog, social, newsletter, communities)?

   **E. Your own read** *(the part that makes the analysis specific)*
   - Where do you think you are genuinely strong?
   - Where do you think you are weak?
   - Who do you lose deals to, and what reason do customers give?
   - What are your goals for the next 6–12 months?

3. **Probe thin answers once.** "Small businesses" is not an ICP. Ask for the size,
   role and trigger. A vague profile produces a vague competitor set, and the cost
   of that shows up much later.

4. **Write `profile/company.md`** — prose, organised under the headings above. This is
   the user's document; they should be able to edit it directly.

5. **Write `profile/company.json`** — the structured subset tools consume:
   ```json
   {
     "name": "", "website": "", "category": "",
     "one_liner": "", "differentiator": "",
     "offerings": [], "icp": { "company_size": "", "roles": [], "industries": [], "geographies": [] },
     "problem_solved": "",
     "pricing": { "model": "", "tiers": [], "typical_deal_size": "", "contract_length": "" },
     "channels": { "active": [], "tried_and_dropped": [], "sales_motion": "" },
     "self_assessment": { "strengths": [], "weaknesses": [], "lose_deals_to": [], "loss_reasons": [] },
     "goals_6_12_months": [],
     "search_terms": []
   }
   ```
   `search_terms` is derived, not asked: the phrases a buyer would actually search.
   These seed `discover_competitors.md`.

6. **Read it back.** Summarise the profile to the user and get confirmation before
   any discovery runs. Everything downstream inherits errors made here.

## Output
`profile/company.md` and `profile/company.json`, confirmed by the user.

## Edge cases & failure handling
- **User is unsure of their own ICP** → capture the uncertainty explicitly rather than
  picking one. Record `"icp_confidence": "low"` and treat the first competitor set as
  a hypothesis to review.
- **No public website yet** → skip step 1 and interview cold. Note that positioning
  comparison will be weaker with nothing of theirs to compare against.
- **User declines to share pricing** → record `"pricing": {"disclosed": false}`. The
  pricing section will compare competitors to each other and omit the user's row,
  rather than guessing.
- **Profile drifts out of date** → re-run this workflow. Do not patch `company.json`
  by hand; the prose and structured forms must stay in agreement.

## Learned constraints

*(none recorded yet — append dated entries as they emerge)*
