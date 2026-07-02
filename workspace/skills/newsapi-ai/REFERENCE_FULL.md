---
name: newsapi-ai
description: Search and analyze news via NewsAPI.ai (Event Registry). Use ONLY when the user explicitly asks for news articles, headlines, news trends, story coverage analysis, or wants to track topics/entities across published news. Do NOT use for general web search (use web search), competitor research from non-news sources, social media monitoring, or anything that does not specifically require published-news coverage. Returns structured article hits and concept/sentiment metadata; the model does the semantic interpretation.
metadata: {"openclaw":{"emoji":"📰","os":["linux"],"requires":{"bins":["curl","jq"]},"primaryEnv":"NEWSAPI_AI_KEY"}}
---

# NewsAPI.ai (Event Registry) — news search & trend analysis

This skill uses the [NewsAPI.ai](https://newsapi.ai) (Event Registry) v1 API to fetch articles, trending events, and concept/category metadata. The API does the retrieval and structured tagging; **you (the model) do the semantic interpretation** — themes, narrative arcs, sentiment shifts, and trend calls.

## When to invoke

Yes:
- "What's the news on <topic> this week?"
- "Find articles about <company/person/event> from the last N days"
- "Summarize the trend in coverage of <topic>"
- "What concepts/entities co-occur with <topic> in recent news?"
- "Compare how <source A> and <source B> covered <event>"

No:
- General web search → use the web search tool, not this skill.
- Real-time stock quotes, weather, sports scores → wrong tool.
- Internal product/company knowledge → use `product-context` or repo docs.
- Anything not actually about *published news articles*.

## Auth & config

Source of truth: `/home/aaron/.openclaw/credentials/news-api-ai.json`.

Gateway behavior:
- `openclaw.json` reads the API key directly from that JSON file through `secrets.providers.newsapi`.
- The `newsapi-credentials` startup hook also exports `NEWSAPI_AI_KEY` into the gateway process for shell-oriented workflows, but gateway boot no longer depends on that env var existing first.
- No `.env` file is required.

Current config in `openclaw.json`:

```json
"skills": {
  "entries": {
    "newsapi-ai": {
      "enabled": true,
      "apiKey": { "source": "file", "provider": "newsapi", "id": "/API key" }
    }
  }
}
```

For ad hoc shell use outside the gateway process, load the key from the credential file first:

```bash
export NEWSAPI_AI_KEY="$(jq -r '."API key"' /home/aaron/.openclaw/credentials/news-api-ai.json)"
```

If the startup hook has already run, the variable should already be present inside gateway-triggered exec sessions.

## Endpoints used

| Purpose | Endpoint |
|---|---|
| Article search | `POST https://eventregistry.org/api/v1/article/getArticles` |
| Event search (story clusters) | `POST https://eventregistry.org/api/v1/event/getEvents` |
| Trending concepts | `POST https://eventregistry.org/api/v1/trends/getTrendingConcepts` |
| Concept suggest (resolve "Apple" → URI) | `POST https://eventregistry.org/api/v1/suggestConceptsFast` |

Always POST JSON. Always include `apiKey`. Always pass `dataType: ["news"]` unless you specifically want blogs/PR.

## Patterns

### 1. Recent articles on a topic (last 7 days, English, top sources)

```bash
curl -s -X POST https://eventregistry.org/api/v1/article/getArticles \
  -H 'Content-Type: application/json' \
  -d '{
    "apiKey": "'"$NEWSAPI_AI_KEY"'",
    "query": {
      "$query": {
        "$and": [
          {"keyword": "<TOPIC>", "keywordLoc": "title,body"},
          {"lang": "eng"},
          {"dateStart": "<YYYY-MM-DD>", "dateEnd": "<YYYY-MM-DD>"}
        ]
      }
    },
    "resultType": "articles",
    "articlesSortBy": "rel",
    "articlesCount": 25,
    "articlesIncludeArticleConcepts": true,
    "articlesIncludeArticleCategories": true,
    "articlesIncludeArticleSentiment": true,
    "dataType": ["news"]
  }' | jq '.articles.results[] | {title, url, source: .source.title, date, sentiment, concepts: [.concepts[].label.eng] | .[:5]}'
```

### 2. Event clusters (one row per *story*, not per article)

Use when the user wants to understand the *shape* of coverage rather than read every article.

```bash
curl -s -X POST https://eventregistry.org/api/v1/event/getEvents \
  -H 'Content-Type: application/json' \
  -d '{
    "apiKey": "'"$NEWSAPI_AI_KEY"'",
    "query": {"keyword": "<TOPIC>", "lang": "eng", "dateStart": "<YYYY-MM-DD>"},
    "resultType": "events",
    "eventsCount": 20,
    "eventsSortBy": "size",
    "eventsIncludeEventConcepts": true,
    "eventsIncludeEventCategories": true
  }' | jq '.events.results[] | {title: .title.eng, date: .eventDate, articleCount, concepts: [.concepts[].label.eng] | .[:5]}'
```

### 3. Trending concepts (semantic trends)

```bash
curl -s -X POST https://eventregistry.org/api/v1/trends/getTrendingConcepts \
  -H 'Content-Type: application/json' \
  -d '{
    "apiKey": "'"$NEWSAPI_AI_KEY"'",
    "source": "news",
    "count": 20,
    "conceptType": ["person", "org", "loc"]
  }' | jq '.trendingConcepts[] | {label: .label.eng, type, score: .trendingScore}'
```

### 4. Resolve a topic name to a concept URI (better than keyword search)

```bash
curl -s -X POST https://eventregistry.org/api/v1/suggestConceptsFast \
  -H 'Content-Type: application/json' \
  -d '{"apiKey": "'"$NEWSAPI_AI_KEY"'", "prefix": "<TOPIC>", "lang": "eng"}' \
  | jq '.[0:5] | .[] | {label: .label.eng, uri, type}'
```

Then use the returned `uri` in the article query as `{"conceptUri": "<URI>"}` instead of `{"keyword": "..."}` — much more precise.

## Output discipline

Default response shape Jerry should produce after a query:

1. **Summary** (2–4 sentences): the trend/headline takeaway.
2. **Top sources** (3–5 bullets): title — outlet — date — 1-line angle.
3. **Co-occurring concepts** (top 5): the entities/orgs/places that appear most with the topic.
4. **Sentiment skew** (one line): if `articlesIncludeArticleSentiment: true` was used.
5. **Drill-down link**: a NewsAPI.ai search URL for the user to explore manually.

Do NOT dump raw JSON. Do NOT paste >5 articles. Do NOT include articles without source attribution.

## Cost / rate awareness

- Each request consumes API tokens. Default to `articlesCount: 25` (not 100+) unless asked.
- Cache the same query within a session — don't re-run identical searches.
- Use `suggestConceptsFast` once to resolve a name, then reuse the URI.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `401 Unauthorized` | Key missing/expired | Refresh `NEWSAPI_AI_KEY` env, retry once |
| Empty `.articles.results` | Date window too narrow or keyword mismatch | Widen date range; try `suggestConceptsFast` to find the canonical URI |
| `error: API tokens exhausted` | Monthly quota hit | Stop. Report to Aaron. Do not retry. |
| Slow (>10s) response | Large `articlesCount` or broad query | Lower count, narrow keywords, add `lang: "eng"` |

## Don'ts

- Don't paginate beyond page 2 without a specific reason.
- Don't translate non-English articles unless the user asked.
- Don't claim "trending" without using `getTrendingConcepts` or comparing event counts across windows.
- Don't speculate about article content you didn't fetch.
