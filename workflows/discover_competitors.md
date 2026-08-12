# Discover Competitors

## Objective
Auto-discover the competitor set from the business profile, cluster it into tiers, and get
user approval before any deep research runs. Produces `research/competitors.json`.

Run at setup, then re-run quarterly to catch new entrants.

## Inputs
| Input | Required | Notes |
|---|---|---|
| `profile/company.json` | yes | Run `capture_business_profile.md` first |
| Existing `research/competitors.json` | no | On re-runs, used to detect what is new |

## Steps

1. **Search from several angles, not one.** A single query returns one slice of the
   market. Run all of these and pool the results:

   | Angle | Query shape |
   |---|---|
   | Category + geography | `"<category>" <geography>` |
   | Buyer problem language | the customer's words from `problem_solved`, not industry jargon |
   | Alternatives | `alternatives to <user's company>`, `<competitor> vs` |
   | Directories & review sites | G2, Capterra, Clutch, industry-specific directories |
   | Comparison content | `best <category> for <ICP>` — surfaces who is being written about |
   | The user's own losses | every name in `self_assessment.lose_deals_to` |

   Names from `lose_deals_to` go in automatically. The user already knows those are
   real competitors; discovery is for finding the ones they do not know about.

2. **Filter against the ICP before the offer.** The common failure is matching on
   surface similarity — a company that sells something similar to a completely
   different buyer is not a competitor. Check `icp` first: same buyer, then same offer.

3. **Cluster into three tiers.**
   - **Direct** — same offer, same buyer. These get the deepest research.
   - **Adjacent** — different offer, same buyer. They compete for the same budget.
   - **Aspirational** — where the market is heading; who the user might become.

4. **Attach evidence to every candidate.** Each entry needs a one-line rationale and
   a URL proving it exists and does what you claim. A competitor you cannot evidence
   is a guess, and guesses are what make a report untrustworthy.

5. **Cap the set at 5–7 total.** This is the main cost and effort driver. If more look
   worth tracking, present the overflow to the user and let them choose — do not
   silently widen scope.

6. **Identify the pages to track per competitor.** Aim for 4–6:
   `home`, `pricing`, `product`/`features`, `about`, plus `blog` or `customers` if
   they carry signal. Verify each URL resolves before recording it — a 404 in
   `competitors.json` will fail every future monthly run.

7. **Present the set to the user and get explicit approval.** Show tier, rationale and
   evidence URL for each. This is the gate. Nothing deep runs before it.

8. **Write `research/competitors.json`:**
   ```json
   {
     "approved_on": "YYYY-MM-DD",
     "approved_by_user": true,
     "competitors": [
       {
         "slug": "acme-corp",
         "name": "Acme Corp",
         "tier": "direct",
         "rationale": "",
         "evidence_url": "",
         "pages": { "home": "https://…", "pricing": "https://…" },
         "pricing_gated": false,
         "notes": ""
       }
     ]
   }
   ```

## Output
`research/competitors.json` with `approved_by_user: true`. On re-runs, report which
entries are new, which are unchanged, and which look dormant.

## Edge cases & failure handling
- **Fewer than 3 direct competitors found** → either the category description is too
  narrow, or the user is in a genuinely thin market. Re-read `company.json` and widen
  the search terms before concluding the latter.
- **Discovery surfaces a giant** (AWS-scale player in an SMB market) → classify as
  aspirational, not direct. Comparing pricing against them produces noise.
- **Competitor has no public pricing** → set `pricing_gated: true` and drop the
  pricing page from their tracked set. The report will state pricing is not published
  rather than inferring a number.
- **Two candidates are the same company** (rebrand, acquisition, regional domain) →
  keep one, note the alias.
- **User rejects most of the set** → the profile's category or ICP is wrong. Go back to
  `capture_business_profile.md` rather than searching harder.

## Learned constraints

*(none recorded yet — append dated entries as they emerge)*
