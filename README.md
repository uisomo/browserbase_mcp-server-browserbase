# Browserbase MCP Server

![cover](assets/cover-mcp.png)

[The Model Context Protocol (MCP)](https://modelcontextprotocol.io/introduction) is an open protocol that enables seamless integration between LLM applications and external data sources and tools. Whether you’re building an AI-powered IDE, enhancing a chat interface, or creating custom AI workflows, MCP provides a standardized way to connect LLMs with the context they need.

This server provides cloud browser automation capabilities using [Browserbase](https://www.browserbase.com/), [Puppeteer](https://pptr.dev/), and [Stagehand](https://github.com/browserbase/stagehand) (Coming Soon). This server enables LLMs to interact with web pages, take screenshots, and execute JavaScript in a cloud browser environment.

To learn to get started with Browserbase, check out ['browserbase/README.md'](./browserbase/README.md) or [Stagehand MCP](./stagehand/README.md).

## Getting Started with available MCPs

🌐 **Browserbase MCP** - Located in [`browserbase/`](./browserbase/)

| Feature | Description |
|---------|-------------|
| Browser Automation | Control and orchestrate cloud browsers |
| Data Extraction | Extract structured data from any webpage |
| Console Monitoring | Track and analyze browser console logs |
| Screenshots | Capture full-page and element screenshots |
| JavaScript | Execute custom JS in the browser context |
| Web Interaction | Navigate, click, and fill forms with ease |

🤘 **Stagehand MCP** - Located in [`stagehand/`](./stagehand/) 

| Feature | Description |
|---------|-------------|
| Atomic Instructions | Execute precise actions like `act("click the login button")` or `extract("find the red shoes")` |
| Model Flexibility | Supports multiple models, including OpenAI's GPT-4 and Anthropic's Claude-3.5 Sonnet |
| Modular Design | Easily integrate new models with minimal changes |
| Vision Support | Use annotated screenshots for complex DOMs |
| Open Source | Contribute to the project and join the [Slack community](https://join.slack.com/t/stagehand-dev/shared_invite/zt-2uvuobu50-~wVSx2Si75CPa3332hwVEw) for support |

### Alternative Installation Methods

[Smithery](https://smithery.ai/protocol/@browserbasehq)

## Weekly G10 Market Summary Script

The repository also includes a standalone Python helper for producing a weekly cross-asset
market summary covering the G10. The script gathers equity, credit, FX, and rates proxies,
calculates the change between the start and end of the latest trading week, and can send the
result by email via a Zoho account.

### Requirements

```bash
pip install -r requirements.txt
```

The script expects Zoho SMTP credentials and the recipient list to be provided via
environment variables:

* `ZOHO_EMAIL` – the Zoho email address used to send the report.
* `ZOHO_EMAIL_PASSWORD` or `ZOHO_EMAIL_APP_PASSWORD` – the password/app password for that
  account.
* `ZOHO_EMAIL_RECIPIENTS` – comma separated list of recipients.

### Usage

Preview the email body without sending:

```bash
python scripts/weekly_market_summary.py --preview
```

Send the summary via Zoho (also prints a copy to stdout):

```bash
python scripts/weekly_market_summary.py --send --subject "Weekly Market Wrap"
```

> **Note:** Some sovereign rate proxies rely on ETF substitutes where direct yield tickers are
> unavailable on Yahoo Finance. If a market lacks coverage or an instrument fails to download,
> the script will warn and continue with the remaining data points.
