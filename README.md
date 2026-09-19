# MOEX Trade Bot — Sandbox scaffold

Каркас торговой автоматизации на GitHub Actions + T-Invest API + e-mail handoff.

## Текущий режим

**Только T-Invest Sandbox. Реальные сделки отключены конструктивно.**
В коде нет переключателя на production endpoint.

Схема:

1. `strategy.py` в будущем находит потенциальный вход.
2. GitHub отправляет письмо `[TRADE-SIGNAL] ...` с данными сигнала.
3. Внешний reviewer (в будущем ChatGPT/другой сервис) проверяет сигнал.
4. Reviewer отправляет письмо `[TRADE-CMD] ...` в строго заданном JSON-формате.
5. GitHub читает команду из почты.
6. Команда проходит проверки:
   - UUID `signal_id`;
   - HMAC-токен;
   - срок действия;
   - разрешённое действие;
   - только LIMIT-ордер;
   - количество лотов > 0;
   - торговля включена;
   - только Sandbox.
7. Только после этого вызывается `PostSandboxOrder`.

## Важно про ChatGPT без API

GitHub-часть и почтовый протокол не требуют OpenAI API.
Но для полностью автоматического шага «пришло письмо → ChatGPT немедленно анализирует → отправляет ответ»
нужен внешний триггер, который умеет запускать модель. В текущем подключении ChatGPT/Gmail такого
мгновенного входящего e-mail триггера нет. Поэтому этот репозиторий готовит интерфейс для него,
но не имитирует его и не автоматизирует веб-интерфейс ChatGPT.

## Secrets

Создайте в GitHub Repository secrets:

- `TINVEST_TOKEN` — токен T-Invest API.
- `TINVEST_SANDBOX_ACCOUNT_ID` — ID sandbox-счёта.
- `TRADE_HMAC_SECRET` — случайная длинная строка, не менее 32 символов.
- `MAIL_USER` — адрес почтового ящика.
- `MAIL_APP_PASSWORD` — app password почты.
- `MAIL_TO` — адрес, куда отправлять сигналы.
- `COMMAND_ALLOWED_FROM` — адрес, от которого разрешено принимать команды.

Для Gmail:
- IMAP: `imap.gmail.com:993`
- SMTP: `smtp.gmail.com:465`

## Variables

Можно задать Repository variables:

- `TRADING_ENABLED=false` — по умолчанию торговля выключена.
- `COMMAND_MAX_AGE_MINUTES=15`
- `MAIL_IMAP_HOST=imap.gmail.com`
- `MAIL_SMTP_HOST=smtp.gmail.com`

## Первый запуск

1. Создайте sandbox-счёт в T-Invest.
2. Пополните его тестовыми рублями.
3. Добавьте secrets.
4. Оставьте `TRADING_ENABLED=false`.
5. Запустите workflow `Send test signal`.
6. Сформируйте ответное письмо по примеру.
7. Запустите workflow `Process trade commands`.
8. После проверки включите `TRADING_ENABLED=true` — всё ещё только Sandbox.

## Почему workflow пока без расписания

Стратегию и частоту сканирования мы определим отдельно.
Это позволяет не расходовать GitHub Actions minutes до того, как торговая логика готова.
