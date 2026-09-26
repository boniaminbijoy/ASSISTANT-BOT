# Telegram Official Sponsored Ads

This project uses Telegram's native sponsored-message monetization model.

## What the bot does

- All users keep access to the bot tools.
- No Premium tier is implemented.
- No daily usage quota is implemented.
- No custom advertisement message is injected into tool results.
- No “watch an ad to unlock” gate is implemented.

## Why

Telegram's official sponsored messages for bots are delivered and displayed by Telegram clients. The bot itself cannot reliably force an official sponsored message to appear immediately before a particular tool operation or verify that a user viewed it.

## Monetization

Telegram documents revenue sharing for ads displayed in bots. Eligible bot owners can receive 50% of the revenue from ads displayed in their bots, subject to Telegram's current rules and eligibility.

## Operational setup

1. Deploy the bot normally.
2. Keep the bot public/active and comply with Telegram's bot and monetization policies.
3. Check Telegram's current Ads / Revenue interface for the bot's eligibility and revenue balance.
4. Telegram handles sponsored-message delivery in supported clients.

## Official documentation

- https://core.telegram.org/api/sponsored-messages
- https://core.telegram.org/api/revenue
- https://core.telegram.org/bots/features
