# Elite / ResiLife Project Brief

Last updated: 2026-04-12

## Naming & Brand

- **EWAG** — Elite Wellness Amenity Group. The business entity / client company.
- **ResiLife** — The app we are building. This is the resident- and owner-facing product name. (Codebase still uses `EliteProAI` / `EliteProAIDemo` in places — being phased out.)
- **Elite Wellness** / **Elite Home Fitness** — Legacy/marketing brand names used on the EWAG website. Not the app name.
- **Redstone Laboratories (RSL)** — Aaron, Taylor, Jerry. The development org building ResiLife for EWAG.

## EWAG's Business (from elitewellnessamenitygroup.com)

EWAG is an **AI-powered wellness infrastructure company for multifamily real estate**. Their pitch:

> Transform underutilized property gyms into premium, revenue-generating wellness amenities through human-led activation, resident engagement intelligence, and real-time performance reporting — helping owners support rent strategy without CapEx, operational burden, or renovations.

### What EWAG actually sells to property owners

1. **On-site certified personal trainers** (available 5am–8pm) who deliver 1-on-1 coaching, group fitness classes, and wellness programming inside the building's existing gym.
2. **Nutrition coaching & wellness events** — nutritionists, meal guidance, community wellness activities.
3. **Resident rewards & engagement** — gamified credit system that incentivizes participation and builds habit loops.
4. **ResiLife owner reporting** — real-time dashboard showing utilization, engagement, retention, and amenity ROI so owners can prove the investment drives NOI.
5. **Zero CapEx, zero buildouts, zero operational burden** — EWAG staffs and manages everything; the property just provides the space.

### EWAG's value propositions (the numbers that matter)

- **$130–$400+ rent premium per unit/month** for properties with activated wellness amenities (market-dependent)
- **20–40% faster lease-up velocity** vs. buildings with unstaffed gyms
- **3× higher resident satisfaction** with live wellness vs. passive fitness rooms
- **+68% amenity utilization growth** after activation
- **87% resident participation rate**, **67% repeat engagement**
- **Even 5% retention improvement** has measurable NOI impact
- **Cap rate leverage**: extra $100K NOI at 5% cap = $2M added asset value
- **76% of renters** say they'd choose a property with wellness services over one without
- **10–25%+ rent premium** for wellness-focused residential properties (Global Wellness Institute 2025)
- **~15% effective rent increase** for buildings investing in wellness staffing (MIT Healthy Buildings 2025)

### EWAG's target customer

- **Class A and Class B multifamily communities with 150+ units** and existing fitness amenities
- Property managers, asset managers, portfolio directors, REIT decision-makers
- The pitch is designed for **developers/owners, regional/area property managers, and investors**

### The core problem EWAG solves

Most residential gyms are "stagnant amenities" — spaces that exist but fail to influence resident behavior or leasing decisions:
- Low/no resident usage
- No staffing or live programming
- No emotional or social connection
- No measurable impact on retention, NOI, or leasing
- Treated as a checkbox amenity instead of an asset

EWAG's model: **Programs > Rooms.** "Residents don't renew because of treadmills — they renew because they feel connected."

### What's included in the EWAG wellness partnership

| Pillar | What it means |
|--------|---------------|
| 🏋️ On-site coaching & group fitness | Certified trainers, 1-on-1 sessions, group classes, in-building |
| 🥗 Nutrition coaching & wellness events | Nutritionists, meal plans, community wellness activities |
| 🎁 Resident rewards & engagement | Gamified credit-based system driving participation habits |
| 📊 ResiLife owner reporting | Real-time utilization, engagement, retention, amenity performance dashboards |
| 🏢 No CapEx / no buildouts / no ops burden | EWAG manages all staffing, programming, tech — property just provides space |

### EWAG's competitive positioning

- "White-glove, human-first service" — not a SaaS platform, not just an app
- The app (ResiLife) is the **digital layer** on top of a **human-led wellness operation**
- "All under your brand — powered by ResiLife" — white-labeled to property branding
- Scalable across portfolios with no CapEx per property
- Aligns with REIT performance metrics and investor expectations

## What we are building: ResiLife

ResiLife is the **software platform** that powers EWAG's offering. It has three user roles:

### 1. Resident app (primary)
The resident-facing mobile experience. This is what makes EWAG's wellness programming feel like a **private fitness club**, not just a gym with a trainer.

Primary audiences:
- Residents / members
- Property owners / operators
- Trainers / wellness staff

### 2. Owner dashboard
Real-time analytics proving amenity ROI. This is how EWAG justifies rent premiums and retention claims to property owners.

### 3. Trainer tools
Operations layer for coaches/staff to manage classes, residents, scheduling, and delivery metrics.

## Strategic idea

ResiLife is not just a resident wellness app. It is a **B2B2C amenity activation platform**:
- Resident-facing: wellness + community experience that creates behavior and habit loops
- Owner-facing: dashboard proving utilization / engagement / retention / NOI signals
- Trainer-facing: operations + class / resident management for service delivery

The app must reflect EWAG's core thesis:
- **Human-first**: coaches and real people are the product, the app supports them
- **Measurable**: everything the app does should produce data that flows to the owner dashboard
- **Engagement-driven**: rewards, community, challenges — build habits, not just features
- **White-label ready**: the experience should feel like the property's own wellness program

## Source material reviewed

### Gmail
Forwarded emails reviewed:
- Fwd: X-Lab Intro - Suffolk University
- Fwd: X Lab results
- Fwd: X-Lab EWAG Preliminary Heatmap
- GitHub invite for aarwitz/iosApp

### Google Drive
Accessible shared folder:
- ResiLife UI
  - Resident App
    - Coaching.png / .mp4
    - Home.png / .mp4
    - Community.png / .mp4
  - Owner Dashboard
    - OwnerDashboard.png / .mp4

Note:
- Some newer Google Drive heatmap links from forwarded email were not directly accessible from the connected account at intake time.

### GitHub
Repo accepted and accessible:
- EWAG-dev/iosApp (Linux source: `/home/aaron/repos/EWAG-dev-iosApp`, Mac clone: `/Users/taylorolsen-vogt/iosApp`)

## X-Lab / Suffolk study context

The Suffolk X-Lab study tested front-end layouts / media for:
- Home
- Coaching
- Community
- Owner Dashboard

Study constraints from email:
- UI/UX + navigation only for this phase
- realistic sample data was acceptable
- no backend required for the study phase
- videos were capped roughly at 10–20 seconds each

## Research insights already visible from emails

Strongest takeaways from Jennifer's summary:
- Attention is concentrated in the top half of the screen
- Users are scanning / deciding quickly rather than exploring deeply
- The middle/top-mid area is the decision zone
- Lower-page content receives limited engagement
- Faces / identity elements attract early attention
- Resident and owner experiences should follow the same clarity principle, but with different goals:
  - resident = immediate action
  - owner = immediate understanding

Immediate product implications:
- compress critical information upward
- reduce dependence on scrolling for discovery
- present one dominant action per screen
- make owner dashboards lead with insights, not dense data
- keep human / coach identity prominent where useful

## Current repo understanding

### Tech stack
Frontend:
- SwiftUI iOS app
- centralized AppStore state
- multiple role-based root experiences

Backend:
- Swift Vapor backend
- PostgreSQL
- JWT auth
- Railway staging deployment

Testing / CI:
- XCTest-based tests
- backend tests
- GitHub Actions API contract workflow

### Role-based product structure

#### Resident app
Main tabs visible in code:
- Home
- Coaching
- Nutrition
- Community
- Rewards

Additional resident features in codebase:
- chat / conversations
- profile / settings
- challenges
- bookings / schedule
- QR / connector / friends
- groups / posts / stories
- meal tools / delivery / grocery flows

#### Owner app
OwnerRootView + OwnerStore show a configurable dashboard system with categories:
- Utilization
- Revenue
- Engagement
- Retention
- Operations
- Programs

There is also:
- AI insights overlay / chat
- customizable dashboard cards / pages
- dashboard grid architecture

Owner-side data model aligns tightly with EWAG's business case:
- utilization and amenity penetration
- engagement and repeat use
- retention / renewal intent / NOI signals
- coaching delivery / operations
- program performance

#### Trainer app
Trainer-specific views exist for:
- dashboard
- classes
- community
- residents
- scheduling
- metrics
- operations

This suggests the product is meant to support actual service delivery, not just resident browsing.

## Important docs found in repo

Key product docs:
- PRODUCT_ONE_PAGER.md
- PRODUCT_MARKETING_DOC.md
- ROADMAP.md
- DemoAccounts.md
- CondoOwnerNotes.txt
- SOFT_LAUNCH_CHECKLIST.md

These documents confirm the app is trying to combine:
- resident wellness engagement
- social/community mechanics
- service bookings / coaching
- owner analytics and ROI proof

## Notable tension / likely product question

There appears to be some product sprawl in the resident experience.
The codebase includes many features:
- coaching
- nutrition
- connector / friend matching
- community posts / groups
- rewards
- bookings
- schedules
- chat
- classes
- meal features

Meanwhile, the X-Lab findings suggest:
- less browsing
- clearer top-of-screen decision paths
- stronger prioritization

Likely upcoming strategic question:
- what is the primary resident action on each screen?
- what should be demoted, merged, or hidden behind secondary navigation?

## Product direction (confirmed)

ResiLife must deliver three things:
1. **Resident app**: a high-trust, action-oriented wellness concierge that makes residents *feel* like they have a personal wellness team. Book coaching, track nutrition, earn rewards, connect with neighbors.
2. **Owner dashboard**: real-time proof that the wellness program drives utilization, engagement, retention, and NOI. This is what EWAG sells — the data must be compelling and immediately legible.
3. **Trainer tools**: the execution layer — scheduling, class management, attendance, resident engagement tracking.

**The primary resident actions per screen:**
- **Home**: see today's coaching/class, quick-book a session, check reward progress
- **Coaching**: book a 1-on-1 or group session, see your booked sessions, chat with coach
- **Nutrition**: get meal guidance, log meals, connect with nutritionist
- **Community**: see what neighbors are doing, join challenges, post/react
- **Rewards**: see credits, redeem, understand how to earn more

**What NOT to build / deprioritize:**
- Feature sprawl that doesn't map to EWAG's actual service delivery
- Anything that requires backend capabilities we don't have yet that doesn't serve the demo/pilot
- Speculative features with no path to EWAG's core value props (rent premiums, retention, engagement metrics)

## Near-term goal

Pilot-ready product demo that EWAG can show to property owners and prospects during tours and sales calls. The app needs to:
- Look polished and professional (private-club feel)
- Show realistic data in both resident and owner views
- Demonstrate the full loop: resident uses wellness → data flows to owner dashboard
- Support the narrative: "activated amenities outperform passive gyms"

## Development readiness notes

Current state appears beyond mockup stage:
- role-based navigation exists
- backend exists
- auth exists
- seeded demo accounts exist
- owner endpoints exist
- tests exist

So future work likely falls into one of these buckets:
1. UX tightening and prioritization
2. wiring remaining backend-backed data paths
3. improving analytics realism for owner dashboards
4. polishing role-specific flows
5. reducing demo/prototype inconsistencies and naming drift

## Immediate next steps I can help with

1. Repo audit
- map actual screen/file structure
- identify what is demo-only vs backend-backed
- summarize current maturity by module

2. Product synthesis
- convert X-Lab findings into screen-by-screen recommendations
- resident app: Home / Coaching / Community
- owner dashboard: metrics hierarchy and card priorities

3. Delivery planning
- create prioritized backlog
- separate product decisions from engineering tasks
- identify quickest high-leverage improvements

4. Implementation support
- once directed, make focused code changes in the repo

## Story creation litmus test

Before creating any Task Manager story, Jerry must confirm ALL of these:

1. **Does it serve EWAG's core value chain?** (resident engagement → measurable data → owner ROI proof)
2. **Is it executable?** Can Jerry (or the assignee) actually sit down and complete this in a sprint? Investigation stories are allowed only when they unblock an important executable action.
3. **Is it specific?** A story like "improve app cohesion" fails. A story like "[Home] Replace placeholder greeting with personalized resident name + today's booked session" passes.
4. **Does a similar story already exist?** Search existing issues before creating. If it overlaps with an existing story, update that one instead.
5. **Is it high-ROI?** Estimate impact on EWAG's business ÷ engineering effort. If higher-ROI work exists in the backlog, do that first. Don't create low-ROI stories when the sprint has room for better ones.

## Autonomous product development

Jerry doesn't just execute stories — Jerry runs a continuous product loop:

**Ideate → Research → Prioritize by ROI → Execute → Test → Review → Learn → Repeat**

- Jerry should generate product ideas by comparing the app against EWAG's website, competitors, and market trends
- Jerry should research ideas (web search, competitor analysis) before creating stories
- Jerry should estimate ROI and only create stories for ideas that survive analysis
- Jerry should prototype promising ideas on feature branches and present evidence to Aaron
- Jerry should learn from every build/review cycle and feed insights back into memory and product docs
- Jerry should kill bad ideas before they become tickets — that's not failure, that's discipline

## Summary

ResiLife is the digital + operational platform that powers EWAG's wellness amenity business for multifamily real estate. The app must make residents feel they have a private wellness club, give owners real-time proof of ROI, and give trainers tools to deliver. Every feature and every story should trace back to: **does this help EWAG turn an empty gym into a leasing advantage?**
