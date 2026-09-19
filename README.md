# MOEX Trade Bot — Sandbox scaffold

Каркас торговой автоматизации на GitHub Actions + T-Invest API + e-mail handoff.

## Текущий режим

**Только T-Invest Sandbox. Реальные сделки отключены конструктивно.**
В коде нет переключателя на production endpoint.

Схема:
1. strategy.py в будущем находит потенциальный вход.
2. GitHub отправляет письмо [TRADE-SIGNAL] с данными сигнала.
3. Внешний reviewer проверяет сигнал.
4. Reviewer отправляет письмо [TRADE-CMD] в строгом JSON-формате.
5. GitHub читает команду из почты.
6. Команда проходит проверки UUID, HMAC, срока действия, типа ордера и режима Sandbox.
7. Только после этого вызывается PostSandboxOrder.

## Важно про ChatGPT без API

GitHub-часть и почтовый протокол не требуют OpenAI API.
Для полностью автоматического шага «пришло письмо → ChatGPT немедленно анализирует → отправляет ответ»
нужен внешний триггер, который умеет запускать модель. Текущий каркас готовит интерфейс для такого
reviewer, но не имитирует его.

## GitHub Secrets

- TINVEST_TOKEN
- TINVEST_SANDBOX_ACCOUNT_ID
- TRADE_HMAC_SECRET — случайная строка не короче 32 символов
- MAIL_USER
- MAIL_APP_PASSWORD
- MAIL_TO
- COMMAND_ALLOWED_FROM

Для Gmail:
- IMAP: imap.gmail.com:993
- SMTP: smtp.gmail.com:465

## Repository variables

- TRADING_ENABLED=false
- COMMAND_MAX_AGE_MINUTES=15
- MAIL_IMAP_HOST=imap.gmail.com
- MAIL_SMTP_HOST=smtp.gmail.com

## Первый запуск

1. Добавьте TINVEST_TOKEN как GitHub Secret.
2. Запустите workflow Bootstrap T-Invest Sandbox.
3. Сохраните полученный accountId как TINVEST_SANDBOX_ACCOUNT_ID.
4. Добавьте остальные secrets.
5. Оставьте TRADING_ENABLED=false.
6. Запустите Send test signal.
7. После теста команд включайте TRADING_ENABLED=true — это всё ещё только Sandbox.

## Стратегия

Стратегия намеренно пока не реализована. Частоту сканирования, universe инструментов,
правила входа/выхода и риск-менеджмент определим отдельно.
