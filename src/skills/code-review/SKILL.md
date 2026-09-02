---
name: code-review
description: Review code for bugs, style, and improvements
slash: true
---

# Code Review Skill

This skill helps you review code and provide constructive feedback.

## Usage

Use this skill when the user wants to:
- Review code for bugs and issues
- Check code style and conventions
- Suggest improvements
- Find security vulnerabilities

## Workflow

1. **Read the code file** using the `read` tool
2. **Analyze** for:
   - Correctness and potential bugs
   - Performance and efficiency
   - Security vulnerabilities
   - Code style and maintainability
   - Test coverage
3. **Provide feedback** with specific suggestions

## Review Checklist

- [ ] Logic correctness
- [ ] Error handling
- [ ] Input validation
- [ ] Resource management (files, connections)
- [ ] Security (injection, auth, data exposure)
- [ ] Performance (algorithms, queries)
- [ ] Code style (naming, formatting)
- [ ] Documentation
- [ ] Test coverage

## Output Format

```
## Code Review: <filename>

### Summary
Brief overview of code quality

### Issues Found
1. [Severity] Description
   - Location: line X
   - Suggestion: how to fix

### Positive Aspects
- What's done well

### Recommendations
- Prioritized improvements
```

## Tips

- Be constructive and specific
- Provide code examples for fixes
- Prioritize security and correctness issues
- Acknowledge good practices
