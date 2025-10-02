# YouTube Gemini Summarizer

This utility polls a list of YouTube channels via their RSS feeds, detects new uploads, and summarizes them with Google Gemini. Use it to build a daily digest of videos across the channels you follow.

## Features

- Resolves regular channel URLs, custom handles (e.g. `https://www.youtube.com/@yourchannel`), direct channel IDs, and existing RSS feed URLs.
- Persists processed video IDs to avoid duplicate summaries.
- Generates per-video bullet summaries with Gemini and produces a combined highlight section for the day.
- Saves each digest as a Markdown report.

## Prerequisites

1. **Node.js 18+**
2. **Gemini API key** – set the `GEMINI_API_KEY` environment variable. You can create a key from the [Google AI Studio](https://aistudio.google.com/).

## Configuration

Channel configuration lives in [`config/channels.json`](./config/channels.json):

```json
{
  "geminiModel": "gemini-1.5-flash",
  "channels": [
    {
      "url": "https://www.youtube.com/@YouTubeCreators",
      "name": "YouTube Creators",
      "maxVideos": 2
    }
  ]
}
```

- `url` accepts a channel handle, channel page URL, `UC…` channel ID, or an RSS feed URL.
- `name` overrides the channel title shown in reports (optional).
- `maxVideos` limits how many of the newest uploads to evaluate per run (optional). You can also set a global `maxVideosPerChannel` at the root level of the config.
- `geminiModel` lets you override the Gemini model that should generate summaries (defaults to `gemini-1.5-flash`).

## Usage

```bash
cd youtube
npm install
export GEMINI_API_KEY="your-key-here"
npm run summarize
```

You can optionally pass a different configuration file:

```bash
npm run summarize -- --config ./config/alt-channels.json
```

Each run will:

1. Fetch the latest entries from every configured channel.
2. Skip videos that were already summarized (tracked in [`data/seen_videos.json`](./data/seen_videos.json)).
3. Ask Gemini for a concise, actionable summary of every new upload.
4. Create a combined highlight section capturing shared themes and recommended follow-ups.
5. Save the output to `reports/summary-YYYY-MM-DD.md` and print the same content to the console.

## Automation

To create a daily digest, add a cron entry that executes `npm run summarize` once per day. The script is idempotent: already processed uploads are ignored unless you clear the `data/seen_videos.json` file.

## Notes

- The script only has access to metadata available in the RSS feed (title, description, publish date). Providing Gemini with the full transcript would require additional API calls to YouTube.
- Handle URLs (`https://www.youtube.com/@example`) are resolved to their internal channel IDs via a lightweight HTML fetch. If YouTube changes their page structure you may need to update the resolver.
- The generated reports are Markdown; you can import them into your knowledge base or send via email.
