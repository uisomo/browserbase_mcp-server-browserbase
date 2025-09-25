import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import Parser, { Output, Item } from "rss-parser";
import { GoogleGenerativeAI, GenerativeModel } from "@google/generative-ai";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT_DIR = path.resolve(__dirname, "..");
const DEFAULT_CONFIG_PATH = path.join(ROOT_DIR, "config", "channels.json");
const DATA_DIR = path.join(ROOT_DIR, "data");
const REPORTS_DIR = path.join(ROOT_DIR, "reports");
const SEEN_VIDEOS_PATH = path.join(DATA_DIR, "seen_videos.json");

interface ChannelConfig {
  url: string;
  name?: string;
  maxVideos?: number;
}

interface AppConfig {
  geminiModel?: string;
  maxVideosPerChannel?: number;
  channels: ChannelConfig[];
}

type SeenVideos = Record<string, string>;

interface YouTubeFeedItem extends Item {
  id?: string;
  "yt:videoId"?: string;
  mediaGroup?: {
    "media:description"?: string;
    "media:title"?: string;
  };
  author?: string;
}

interface VideoDetails {
  id: string;
  title: string;
  link: string;
  description: string;
  publishedAt: string;
  channelTitle: string;
}

interface VideoSummary extends VideoDetails {
  summary: string;
}

function getConfigPathFromArgs(): string {
  const configFlagIndex = process.argv.findIndex((arg) =>
    ["--config", "-c"].includes(arg)
  );

  if (configFlagIndex !== -1 && process.argv[configFlagIndex + 1]) {
    return path.resolve(process.cwd(), process.argv[configFlagIndex + 1]);
  }

  return DEFAULT_CONFIG_PATH;
}

async function ensureDirectories(): Promise<void> {
  await fs.mkdir(DATA_DIR, { recursive: true });
  await fs.mkdir(REPORTS_DIR, { recursive: true });
}

async function loadConfig(configPath: string): Promise<AppConfig> {
  const raw = await fs.readFile(configPath, "utf-8");
  const config = JSON.parse(raw) as AppConfig;
  if (!config.channels || !Array.isArray(config.channels)) {
    throw new Error("Config file must include a 'channels' array.");
  }
  return config;
}

async function loadSeenVideos(): Promise<SeenVideos> {
  try {
    const raw = await fs.readFile(SEEN_VIDEOS_PATH, "utf-8");
    return JSON.parse(raw) as SeenVideos;
  } catch (error: unknown) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return {};
    }
    throw error;
  }
}

async function saveSeenVideos(seen: SeenVideos): Promise<void> {
  await fs.writeFile(SEEN_VIDEOS_PATH, JSON.stringify(seen, null, 2));
}

function extractVideoId(item: YouTubeFeedItem): string | null {
  if (item["yt:videoId"]) {
    return item["yt:videoId"]!;
  }

  if (item.id && item.id.includes(":")) {
    const candidate = item.id.split(":").pop();
    if (candidate) {
      return candidate;
    }
  }

  if (item.link) {
    const match = item.link.match(/[?&]v=([^&#]+)/);
    if (match) {
      return match[1];
    }
  }

  return null;
}

async function fetchChannelIdFromPage(url: string): Promise<string | null> {
  const response = await fetch(url, {
    headers: {
      "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    },
  });

  if (!response.ok) {
    throw new Error(`Failed to load channel page ${url}: ${response.status}`);
  }

  const html = await response.text();
  const match = html.match(/"channelId":"(UC[^"]+)"/);
  return match ? match[1] : null;
}

async function resolveFeedUrl(channelUrl: string): Promise<string> {
  if (channelUrl.startsWith("http")) {
    const url = new URL(channelUrl);
    if (!url.hostname.includes("youtube.com")) {
      throw new Error(`Unsupported channel host: ${url.hostname}`);
    }

    if (url.pathname === "/feeds/videos.xml") {
      return channelUrl;
    }

    const segments = url.pathname.split("/").filter(Boolean);
    if (segments[0] === "channel" && segments[1]) {
      return `https://www.youtube.com/feeds/videos.xml?channel_id=${segments[1]}`;
    }

    if (segments[0] === "user" && segments[1]) {
      return `https://www.youtube.com/feeds/videos.xml?user=${segments[1]}`;
    }

    if (segments[0]?.startsWith("@")) {
      const channelId = await fetchChannelIdFromPage(channelUrl);
      if (!channelId) {
        throw new Error(
          `Unable to determine channel ID for handle: ${channelUrl}`
        );
      }
      return `https://www.youtube.com/feeds/videos.xml?channel_id=${channelId}`;
    }

    if (segments.length > 0) {
      const channelId = await fetchChannelIdFromPage(channelUrl);
      if (!channelId) {
        throw new Error(`Unable to resolve channel ID for URL: ${channelUrl}`);
      }
      return `https://www.youtube.com/feeds/videos.xml?channel_id=${channelId}`;
    }
  }

  if (channelUrl.startsWith("UC")) {
    return `https://www.youtube.com/feeds/videos.xml?channel_id=${channelUrl}`;
  }

  throw new Error(`Unsupported channel URL: ${channelUrl}`);
}

function normaliseDescription(item: YouTubeFeedItem): string {
  const pieces: string[] = [];
  if (item.contentSnippet) {
    pieces.push(item.contentSnippet);
  }
  const mediaDescription = item.mediaGroup?.["media:description"];
  if (mediaDescription) {
    pieces.push(mediaDescription);
  }
  return pieces.join("\n\n").trim();
}

function resolveChannelTitle(
  feed: Output<YouTubeFeedItem>,
  channelConfig: ChannelConfig
): string {
  return (
    channelConfig.name ||
    feed.title ||
    channelConfig.url
  );
}

function buildVideoDetails(
  feed: Output<YouTubeFeedItem>,
  item: YouTubeFeedItem,
  channelConfig: ChannelConfig
): VideoDetails | null {
  const id = extractVideoId(item);
  if (!id) {
    return null;
  }
  const title = item.title ?? "Untitled Video";
  const link = item.link ?? `https://www.youtube.com/watch?v=${id}`;
  const description = normaliseDescription(item);
  const publishedAt = item.isoDate || item.pubDate || new Date().toISOString();
  const channelTitle = resolveChannelTitle(feed, channelConfig);

  return { id, title, link, description, publishedAt, channelTitle };
}

function getGeminiModel(apiKey: string, modelName: string): GenerativeModel {
  const genAI = new GoogleGenerativeAI(apiKey);
  return genAI.getGenerativeModel({ model: modelName });
}

async function summarizeVideo(
  model: GenerativeModel,
  video: VideoDetails
): Promise<string> {
  const prompt = `You are a helpful assistant that creates concise daily summaries of new YouTube videos. Summarize the video below in 3 to 5 bullet points and include one actionable insight. Focus on the key ideas rather than promotional language.

Title: ${video.title}
Channel: ${video.channelTitle}
Published at: ${video.publishedAt}
URL: ${video.link}

Video description:
${video.description || "(No description provided)"}`;

  const result = await model.generateContent({
    contents: [
      {
        role: "user",
        parts: [{ text: prompt }],
      },
    ],
    generationConfig: {
      temperature: 0.3,
      maxOutputTokens: 512,
    },
  });

  const text = result.response.text();
  if (!text) {
    throw new Error(`Gemini returned an empty summary for video ${video.id}`);
  }
  return text.trim();
}

async function summarizeDailyHighlights(
  model: GenerativeModel,
  summaries: VideoSummary[],
  date: string
): Promise<string> {
  const combinedInput = summaries
    .map(
      (summary, index) =>
        `Video ${index + 1}: ${summary.title} by ${summary.channelTitle}\nSummary:\n${summary.summary}`
    )
    .join("\n\n");

  const prompt = `You are preparing a digest for a team that follows several YouTube channels. Using the video summaries below, provide:
- A short paragraph capturing the shared themes or major developments for ${date}.
- A bulleted list of up to three recommended follow-up actions or items worth watching.

Keep the tone professional and concise.

${combinedInput}`;

  const result = await model.generateContent({
    contents: [
      {
        role: "user",
        parts: [{ text: prompt }],
      },
    ],
    generationConfig: {
      temperature: 0.35,
      maxOutputTokens: 400,
    },
  });

  const text = result.response.text();
  if (!text) {
    throw new Error("Failed to generate combined highlights summary");
  }
  return text.trim();
}

function createReport(date: string, highlights: string, videos: VideoSummary[]): string {
  const header = `# YouTube Daily Summary (${date})`;
  const highlightSection = `## Combined Highlights\n${highlights}`;
  const videoSections = videos
    .map((video) => {
      return `### ${video.channelTitle} — ${video.title}\n- **Published:** ${new Date(
        video.publishedAt
      ).toLocaleString()}\n- **Link:** ${video.link}\n\n${video.summary}`;
    })
    .join("\n\n");

  return [header, highlightSection, "## Video Summaries", videoSections]
    .filter(Boolean)
    .join("\n\n");
}

async function writeReport(report: string, date: string): Promise<string> {
  const filename = `summary-${date}.md`;
  const filePath = path.join(REPORTS_DIR, filename);
  await fs.writeFile(filePath, report, "utf-8");
  return filePath;
}

async function fetchChannelFeed(
  parser: Parser<{}, YouTubeFeedItem>,
  channel: ChannelConfig
): Promise<Output<YouTubeFeedItem>> {
  const feedUrl = await resolveFeedUrl(channel.url);
  return parser.parseURL(feedUrl);
}

async function main(): Promise<void> {
  await ensureDirectories();
  const configPath = getConfigPathFromArgs();
  const config = await loadConfig(configPath);
  const geminiApiKey = process.env.GEMINI_API_KEY;
  if (!geminiApiKey) {
    throw new Error("GEMINI_API_KEY environment variable is required");
  }
  const modelName = config.geminiModel ?? "gemini-1.5-flash";
  const geminiModel = getGeminiModel(geminiApiKey, modelName);
  const parser: Parser<{}, YouTubeFeedItem> = new Parser({
    customFields: {
      item: [["media:group", "mediaGroup"], ["yt:videoId", "yt:videoId"]],
    },
  });

  const seenVideos = await loadSeenVideos();
  const maxVideosPerChannel = config.maxVideosPerChannel ?? 3;
  const newSummaries: VideoSummary[] = [];

  for (const channel of config.channels) {
    try {
      const feed = await fetchChannelFeed(parser, channel);
      const items = feed.items.slice(0, channel.maxVideos ?? maxVideosPerChannel);
      for (const item of items) {
        const details = buildVideoDetails(feed, item, channel);
        if (!details) {
          continue;
        }
        if (seenVideos[details.id]) {
          continue;
        }
        const summary = await summarizeVideo(geminiModel, details);
        newSummaries.push({ ...details, summary });
        seenVideos[details.id] = details.publishedAt;
      }
    } catch (error) {
      console.error(`Failed to process channel ${channel.url}:`, error);
    }
  }

  if (newSummaries.length === 0) {
    console.log("No new videos found today.");
    await saveSeenVideos(seenVideos);
    return;
  }

  const today = new Date().toISOString().slice(0, 10);
  const highlights = await summarizeDailyHighlights(geminiModel, newSummaries, today);
  const report = createReport(today, highlights, newSummaries);
  const savedPath = await writeReport(report, today);
  await saveSeenVideos(seenVideos);
  console.log(report);
  console.log(`\nReport saved to ${savedPath}`);
}

main().catch((error) => {
  console.error("Failed to generate YouTube summaries:", error);
  process.exitCode = 1;
});
