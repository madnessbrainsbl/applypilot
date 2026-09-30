# ApplyPilot

[English](README.md) | [Русский](README.ru.md)

Локальный помощник для поиска работы из двух частей, каждая со своей SQLite-базой:

- **Автопилот** (`tools/`, `autopilot/`): движок на Python + Playwright. Собирает вакансии из многих источников,
  оценивает их по вашему поисковому профилю, открывает форму отклика, заполняет её из профиля и резюме и отдаёт
  финальный клик вам (или отправляет сам, если это включить). Вопросы, на которые нет правил, уходят в цепочку
  LLM-провайдеров; остальное попадает в очередь к вам.
- **Дашборд** (`src/`): приложение на Next.js, форк [JobSync](https://github.com/Gsync/jobsync) (MIT). Ведёт учёт
  откликов и резюме, а на странице `/dashboard/autopilot` управляет движком: запуск, ответы на вопросы в очереди,
  проверка и включение LLM-провайдеров, живой лог.

Всё работает на вашей машине. Резюме, ответы и пароли уходят только на сайты, куда вы откликаетесь, и к тем
LLM-провайдерам, которых вы включили.

Каждый пользователь скачивает проект и запускает собственную копию на своей машине. После установки он
добавляет свои резюме, профиль, ключи и аккаунты; эти данные не входят в исходники и исключены из Git.
Аккаунты дашборда внутри одной установки управляют общим автопилотом этой установки.

> **Ответственное использование.** Автоматическая отправка может нарушать правила сайтов и отправлять отклики,
> которых вы не хотели. Ассистированный режим (`--review --submit`) сам ничего не отправляет: вы проверяете заполненную форму
> и нажимаете Submit. Движок пытается пройти checkbox-проверки и аудио reCAPTCHA через настроенный API распознавания; нерешённые проверки передаются вам. За каждый отклик,
> отправленный от вашего имени, отвечаете вы.

## Требования

- Python 3.10+ и Google Chrome (или Chromium от Playwright)
- Node.js 24+ (только для дашборда)
- Разрабатывалось и проверялось на Windows. На Linux и macOS должно работать, но часть вспомогательного кода
  (завершение процессов, поиск Chrome) содержит пути под Windows. Сообщайте о проблемах.

## Быстрый старт (автопилот)

```sh
git clone https://github.com/madnessbrainsbl/applypilot.git
cd applypilot

pip install -r requirements.txt
python -m playwright install chromium          # не нужно, если используете установленный Chrome

cp autopilot/config.example.json autopilot/config.json     # CLI создаёт его и сам при первом запуске
```

1. Отредактируйте `autopilot/config.json`:
   - `profile`: имя, email, телефон, ссылки, `country`, `languages`, зарплата, срок уведомления.
   - `apply.require_sponsorship`: `true` или `false`. Пока значение `null`, движок сам не отвечает на вопросы про
     визу и право на работу.
   - `apply.answers`: ваши ответы (`open_to_relocation`, `open_to_onsite` и т. д.).
   - `personas`: по записи на каждую профессию (термины в названии, ключевые слова, путь к резюме).
2. Добавьте резюме как персону (LLM-провайдер составит поисковые термины по PDF, опираясь только на текст резюме;
   без провайдера заполните `personas.<имя>.search` вручную):

   ```sh
   python tools/autopilot.py resume add --file my-cv.pdf --name mycv --role "Backend Engineer"
   ```

3. Положите ключи и пароли в `~/.config/search-work-world/secrets.env` (шаблон: `autopilot/secrets.env.example`).
   Храните секреты в secrets.env; старые поля пароля почты и ключей провайдеров в config.json поддерживаются для совместимости.
4. Проверьте настройку и запускайте:

   ```sh
   python tools/autopilot.py doctor                     # что не хватает или настроено неверно
   python tools/autopilot.py discover                   # собрать вакансии в autopilot/jobs.db
   python tools/autopilot.py apply --dry-run --max 1    # заполнить одну форму, ничего не отправлять
   python tools/autopilot.py apply --review --submit --max 5     # ассистированный режим: Submit нажимаете вы
   python tools/autopilot.py status
   ```

`--submit` нужен для запусков без присмотра. Включайте его, только убедившись на нескольких прогонах `--review`, что
всё заполняется верно. Полный список команд и всех ключей конфига: [autopilot/README.md](autopilot/README.md).

## Дашборд

```sh
npm ci
cp .env.example .env         # затем укажите DATABASE_URL="file:./dev.db" и случайный AUTH_SECRET
python -c "import sqlite3; sqlite3.connect('prisma/dev.db').close()"
npx prisma generate
npx prisma migrate deploy
npm run dev                  # http://localhost:3737
```

При первом входе создайте аккаунт и откройте `/dashboard/autopilot`. Страница запускает `python tools/autopilot.py`;
если интерпретатор называется иначе, задайте `AUTOPILOT_PYTHON`. `docker compose up` запускает оригинальный образ
JobSync, в нём нет страницы автопилота. Возможности и настройки дашборда: [docs/jobsync-dashboard.md](docs/jobsync-dashboard.md)
(на английском).

## Тесты

```sh
python tools/run_tests.py    # пример профиля и временная БД, без личного конфига
npm test                     # юнит-тесты дашборда (vitest)
```

## Приватность и безопасность

- Не коммитьте `autopilot/config.json`, `autopilot/resumes/`, `autopilot/jobs.db`, `autopilot/browser-state.json`
  и файл с секретами. Всё это уже в `.gitignore`. См. [SECURITY.md](SECURITY.md).
- Личные факты в офлайн-правилах требуют профиля или явных ответов. Проверка LLM выявляет часть неподтверждённых
  утверждений; ручная проверка остаётся необходимой. Нерешённые вопросы доступны в `python tools/autopilot.py qa --pending`.
- Один раз просмотрите офлайн-правила ответов (`_OFFLINE_RULES` в `tools/autopilot.py`), чтобы знать, на что
  движок отвечает без вас.

## Благодарности и лицензия

Дашборд построен на [JobSync](https://github.com/Gsync/jobsync) (автор Gsync). Лицензия MIT, см. [LICENSE](LICENSE).
Участие приветствуется: [CONTRIBUTING.md](CONTRIBUTING.md).
