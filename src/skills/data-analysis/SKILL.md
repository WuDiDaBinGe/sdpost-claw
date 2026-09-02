---
name: data-analysis
description: Analyze data files (CSV, Excel, JSON) and generate insights
slash: true
---

# Data Analysis Skill

This skill helps you analyze data files and generate insights.

## Usage

Use this skill when the user wants to:
- Analyze CSV, Excel, or JSON data files
- Generate statistics and summaries
- Create visualizations
- Find patterns and trends in data

## Workflow

1. **Read the data file** using the `read` tool
2. **Understand the structure** - columns, types, size
3. **Analyze** using bash with Python/pandas
4. **Summarize findings** in a clear report

## Example

```
User: "Analyze sales.csv and tell me the top products"

1. read("sales.csv") - understand structure
2. bash("python3 -c 'import pandas as pd; df = pd.read_csv(\"sales.csv\"); print(df.describe())'")
3. Provide analysis and insights
```

## Tips

- Always check data types and missing values first
- Use pandas for complex analysis
- Generate visualizations when helpful
- Provide actionable insights, not just statistics
