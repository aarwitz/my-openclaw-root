---
name: resilife-product
description: ResiLife (EWAG) iOS app product knowledge — features, architecture, views, roadmap, marketing, design decisions, and project context. Use when making product decisions, writing stories, reviewing UI, updating product docs, planning sprints, or discussing ResiLife features with stakeholders.
metadata: {"clawdbot":{"emoji":"🏠"}}
---

# ResiLife Product Knowledge

ResiLife is the digital platform powering EWAG's (Elite Wellness Amenity Group) human-led wellness activation business for multifamily real estate. Resi is the primary iOS app developer. Jerry supports internal-tooling, architecture, and orchestration decisions that unblock delivery.

## Product Identity

- **App name:** ResiLife (Xcode project renamed from EliteProAIDemo — migration complete, all targets now use `ResiLife` prefix)
- **Company:** Elite Wellness Amenity Group (EWAG) — the client
- **EWAG website:** https://www.elitewellnessamenitygroup.com
- **Client contact:** Emails forwarded to Jerry's Gmail (aaronclawrsl@gmail.com)
- **Developer org:** Redstone Laboratories (RSL) — Aaron, Taylor, Jerry
- **Full business context:** See `ELITE_PROJECT_BRIEF.md` in workspace for complete EWAG business model, value props, and metrics

## EWAG's Business Model (critical product context)

EWAG sells **human-led wellness activation** to multifamily property owners:
1. On-site certified personal trainers (5am–8pm), 1-on-1 coaching, group fitness
2. Nutrition coaching & wellness events
3. Resident rewards & engagement programming
4. ResiLife owner reporting — real-time utilization, engagement, retention, amenity ROI
5. Zero CapEx, zero buildouts — EWAG manages everything

**Why this matters for every product decision:** The app is the digital layer on top of a real-world service. Features must support what EWAG actually delivers in buildings. Don't build features that don't map to EWAG's service offering.

**Key sales metrics EWAG uses:** $130–$400+ rent premium/unit/month, 20–40% faster lease-up, 3× resident satisfaction, 68% utilization growth, 87% participation, 67% repeat engagement. The owner dashboard must make these numbers visible and compelling.

**Target buildings:** Class A/B multifamily, 150+ units, existing fitness amenity. White-labeled to property brand.

## Core Value Proposition

B2B2C amenity activation platform:
- **Residents**: Wellness + community in one app — coaching, nutrition, social, rewards — feels like a **private fitness club**
- **Property owners**: Dashboard proving utilization / engagement / retention / NOI — the ROI proof that justifies rent premiums
- **Trainers/staff**: Operations + class + resident management — the execution layer for EWAG's on-site team

## App Tabs & Views (Resident Role)

Tabs are feature-flag gated via `UserFeatureFlags` on the `UserProfile`. The `ConnectorView` is a fallback tab only shown when both coaching AND nutrition are disabled.

| Tab | SwiftUI View | Flag condition | What it does |
|-----|-------------|---------------|--------------|
| Home | `HomeFeedView` | Always | Personalized feed: greeting, status widget, staff carousel, quick actions, community pulse, ways to earn |
| Coaching | `CoachingView` | `coachingEnabled` | Coach carousel, booking flow, booked sessions, WOD, coach tips, chat |
| Nutrition | `NutritionView` | `nutritionEnabled` | Nutritionist carousel, meal suggestions, nutrition journal, quick recipes, delivery partners |
| Community | `CommunityView` | Always | Posts feed, stories, groups, friend discovery, map view of local activities |
| Connector | `ConnectorView` | Fallback only (coaching+nutrition both OFF) | Swipeable neighbor discovery, QR friend add, match overlay |
| Rewards | `RewardsView` | Always | Credits balance, earn opportunities, redemption, tier progress (Bronze→Silver→Gold→Platinum→Elite) |

**Additional key views (accessible via SideMenu or navigation):**
- `SideMenu` / `SideMenuOverlay` — Hamburger overlay: Profile, Settings, Challenges, Notifications, Messages, Bookmarks, Connector, Schedule
- `ChatListView` / `ChatDetailView` / `NewConversationView` — Messaging
- `LuxuryBookingOverlay` / `BookingSessionView` — Staff session booking
- `OnboardingView` — 3-page intro carousel
- `SplashScreenView` — Startup splash
- `LoginView` / `SignUpView` / `VerifyEmailView` / `ResetPasswordView` — Auth flow
- `ScheduleView` — Calendar/schedule from SideMenu
- `ChallengesView` / `ChallengeReservationOverlay` — Fitness challenges
- `ActivityView` / `AllActivitiesView` — Amenity activity browsing
- `GroupClassView` — Group fitness class detail
- `AllWorkoutGuidesView` — Workout guide library
- `HabitsTrackerView` — Daily habit tracker
- `WorkoutLogView` — Workout journal logging
- `BuildingView` (+ `PoolView`, `SpaView`) — Building amenity views (gated by `spaEnabled`/`poolEnabled`)
- `WellnessProductsView` — Wellness product marketplace
- `NutritionJournalView` — Nutrition logging
- `EatSmartDetailView` / `QuickRecipesDetailView` — Meal content details
- `GroupsView` / `CreateGroupView` — Community groups
- `FriendsView` / `FindFriendsView` / `FriendProfileView` / `MeetFriendMealView` — Friends + social meal invites
- `QRScannerView` / `QRCodeView` — QR code scanning for friend-add
- `ComposePostView` / `ComposeStoryView` / `PostDetailView` — Community feed creation
- `BookmarksView` — Saved posts
- `NotificationsView` — In-app notification center
- `BadgeView` — Achievement badges
- `MonthlyTrainerSurveyView` — Monthly trainer-client survey
- `EarnOpportunityDetailView` / `EarnFilterView` / `RedeemFilterView` — Rewards earn/redeem flows
- `YouEarnedRewardsOverlay` / `PointsEarnedOverlay` / `ConnectorRewardOverlay` — Reward celebration overlays
- `ConnectorProfileEditorView` / `ConnectorSettingsView` — Connector profile setup
- `ProfileView` / `EditProfileView` / `SettingsView` / `ChangePasswordView` — Profile & settings
- `PrivacySecurityView` + `PrivacyPolicyView` / `TermsOfServiceView` / `ContactUsView` / `HelpCenterView` — Legal & support
- `EmailPreferencesView` / `LanguagePickerView` / `SupportPageView` — Preferences
- `ActivityReservationOverlay` / `EventReservationOverlay` — Amenity/event booking

## Owner Portal (separate role)

Owner login routes to `OwnerRootView` with `OwnerSideMenu` (side-drawer navigation). Sections:
- **Dashboard** (`DashboardGridView`) — configurable analytics card grid with bar/line/donut charts, heatmaps, metrics, resident interaction; AI Insights card
- **AI Chat** (`OwnerAIChatOverlay`) — conversational analytics assistant
- **NOI Calculator** (`NOICalculatorView`) — financial ROI proof tool with hero banner
- **Buildings** (`OwnerBuildingView`) — building management
- **Event Manager** (`OwnerEventManagerView`) — create/manage events
- **Challenge Manager** (`OwnerChallengeManagerView`) — resident challenge management
- **Resident Behavior** (`OwnerResidentBehaviorView`) — behavioral analytics
- **Amenity Survey Builder** (`AmenitySurveyBuilderView`) — custom resident surveys

Tests: `OwnerUITests` in `ResiLifeUITests`

## Trainer Portal (separate role)

Trainer login routes to `TrainerRootView` with tabs for: Dashboard, Classes, Scheduling, Residents, Community, Metrics, Operations.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| iOS UI | SwiftUI (iOS 16+, iPad-aware) |
| iOS State | `AppStore` ObservableObject + @Published |
| iOS Auth | `AuthService` + `KeychainManager` + JWT |
| iOS Networking | `APIClient` (async/await + URLSession; auto token-refresh; snake_case↔camelCase) |
| iOS Health | HealthKit (`HealthKitService`) |
| iOS Push | APNs (`PushNotificationService`, `SessionNotificationService`) |
| iOS Maps | MapKit + `LocalMapLocationCoordinator` |
| iOS Rewards | Tremendous gift-card API (`TremendousService`) |
| iOS App Review | StoreKit (`ReviewPromptService`) |
| Backend | Swift Vapor 4.89+ on Railway |
| Database | PostgreSQL (Railway-managed; local via Docker) |
| Auth | JWT access + opaque refresh tokens + Bcrypt |
| Backend URL | `https://backend-production-1013.up.railway.app/api/v1` |

## Repository

- **Location on Mac:** `/Users/taylorolsen-vogt/iosApp` on Taylor's Mac (single-repo policy — never clone a second copy)
- **Remote:** `https://github.com/EWAG-dev/iosApp.git` (client GitHub org)
- **ios-agent builds from this repo** — `PROJECT_DIR` points here
- **Never clone a second copy** — use `ios-agent build --branch <name>` for branch switching

### Key directories

```
ResiLife/
  Views/              # All SwiftUI views (Views/Owner/, Views/Trainer/ subdirs)
  Models.swift        # All data models
  Services/           # APIClient, AuthService, KeychainManager, HealthKitService,
                      # TremendousService, PushNotificationService, etc.
  ResiLifeApp.swift   # App entry point
ResiLifeTests/        # Unit tests (AppStoreTests, APIClientTests, ModelTests, etc.)
ResiLifeUITests/
  OwnerUITests.swift       # Owner dashboard screenshot + assertion tests
  ResidentUITests.swift    # Resident tab screenshot + assertion tests
  ResidentInteractionTests.swift  # 25+ interaction tests
  TrainerUITests.swift     # Trainer dashboard screenshot
  UITestHelpers.swift      # Shared helpers (launch args, demo auth, wait utilities)
Backend/
  Sources/App/        # Vapor backend (Controllers/, Models/, Middleware/, Services/)
  Package.swift
  docker-compose.yml
Documentation/
  EarnAndRedeemArchitecture.md  # Earn/redeem rewards architecture
```

### Product docs in repo (Jerry should keep these updated)

| File | Purpose |
|------|---------|
| `PRODUCT_ONE_PAGER.md` | Investor-facing one-pager ($750k–$1.5M seed ask) |
| `PRODUCT_MARKETING_DOC.md` | Full MRD with features, workflows, metrics |
| `ROADMAP.md` | Implementation roadmap (phases 0–7) |
| `ANDROID_GOOGLE_PLAY_ROLLOUT.md` | Android Play Store rollout plan |
| `APNS_DEPLOYMENT_RUNBOOK.md` | APNs cert setup and Railway push notification deployment |
| `TogglingAmenities.md` | How to toggle coaching/nutrition feature flags per building |
| `Documentation/EarnAndRedeemArchitecture.md` | Earn/redeem rewards system architecture |
| `CODENT_HANDOFF_CONTEXT_2026-04-18.md` | Agent handoff context and known issues |
| `DemoAccounts.md` | Test account credentials and roles |
| `CLI_GUIDE.md` | Backend CLI usage guide |
| `api-explorer/README.md` | API explorer for browsing generated OpenAPI spec |

**Note:** `SOFT_LAUNCH_CHECKLIST.md` no longer exists in the repo.

**To update these docs**, edit locally on the gateway then push via branch + PR workflow.

## Rewards Tier System

Defined in `RewardsTierLevel` enum in `ResiLife/Models.swift`. **Note:** "Starter" tier no longer exists.

| Tier | Points threshold | Color |
|------|-----------------|-------|
| **Bronze** | 0 | Brown |
| **Silver** | 1,000 | Slate |
| **Gold** | 3,000 | Brand gold (EPTheme.accent) |
| **Platinum** | 5,000 | Near-black |
| **Elite** | 7,000 | Deep purple |

Credits tracked in `UserCreditsLedger` (backend) / `loadCreditsBalance()` (app). Redeemable items have `requiredTier` gates. Tremendous gift cards have a separate tier gate via `RewardsTierLevel.tier(forGiftCardValue:)`. Full architecture in `Documentation/EarnAndRedeemArchitecture.md`.

## Current Sprint Context

- **Task Manager:** `http://127.0.0.1:8000`
- **Active sprint:** "Elite Wellness App Intake & Planning" (sprint id 2)
- **Jerry** is assigned most issues
- **Aaron** handles product decisions and client communication
- **Taylor** contributes design and marketing review

## Sprint Planning Guidelines

When planning sprints, Jerry should:
1. Review the current backlog and in-progress items
2. Look at the ROADMAP.md for phase priorities
3. **Rank stories by ROI** — estimated impact on EWAG's deal-closing / retention ability ÷ engineering effort
4. Create well-scoped stories with clear acceptance criteria (only after passing the 5 gates in task-manager skill)
5. Balance engineering work with visual polish, but always prioritize highest-ROI items
6. Assign stories appropriately (Jerry for code, Aaron/Taylor for review/design)
7. Keep sprint size manageable (5–8 stories per sprint)

## Product Ideation & Research

Jerry is not just an engineer — Jerry is a product thinker. After every build, review, or client email, Jerry should ask:

### Ideation triggers
- **After screenshots**: "Does this screen sell EWAG's story? Would a property owner be impressed?"
- **After client emails**: "What's the real need behind this request? How does it map to EWAG's value chain?"
- **After competitor research**: "What are Mindbody, ClassPass, building management apps doing that we should learn from?"
- **After reviewing EWAG's website**: "Is our app as polished and compelling as their sales pitch?"

### Research before stories
Before turning an idea into a story, Jerry should:
1. **Web search** for competitor approaches and market data
2. **Check EWAG's website** (elitewellnessamenitygroup.com) to ensure alignment with their messaging
3. **Estimate ROI**: "If we ship this, does it help EWAG close a deal, retain residents, or prove ROI to owners?"
4. **Compare effort vs. impact**: only create the story if ROI is high enough to justify sprint space
5. **Document the reasoning** in the story description so Aaron can see Jerry's thinking

### What Jerry should research
- Competitor wellness/fitness platforms: pricing, features, positioning
- Multifamily real estate trends: what property owners care about, what drives lease-up
- Resident engagement best practices: gamification, community building, habit formation
- Owner analytics: what metrics matter most to REITs, asset managers, property managers
- EWAG's own marketing materials: are we building what they're selling?

### Building a case for new ideas
Jerry can create PRs for product ideas and present them to Aaron:
1. Create a feature branch and prototype the idea
2. Build, screenshot, compare to current state
3. Write up the rationale: problem → research → proposal → evidence
4. Create a story with the PR linked and research embedded
5. Aaron decides whether to merge or iterate

## How Jerry Judges the App

After any code change, use the **ewag-visual-qa** skill for the full capture → evaluate → story-create workflow.
