# Chat 9 Product Workflow Integration Contract — Implementation Addendum

This addendum is part of the **Chat 9 Product Workflow Integration Contract** and supersedes any earlier wording in the base contract that listed Support reply/internal-note separation or versioned Training Academy persistence as unimplemented.

## Support conversation contract now implemented

Chat 9 extends, rather than replaces, the existing `esp_support_center.py` case store through `esp_support_conversations.py`.

Routes:

- `GET /command-center/api/support/cases/{case_id}/messages`
  - member sees only `user_visible` messages;
  - Owner sees `user_visible` and `internal` messages;
  - internal rows are excluded by the SQL query on the member path and are not fetched to the browser.
- `POST /command-center/api/support/cases/{case_id}/messages`
  - member may create only `user_visible` replies;
  - Owner may create user-visible replies or `internal` notes;
  - case privacy is authorised through the existing `SupportCaseStore` boundary.
- `GET /command-center/support/cases/{case_id}`
  - accessible case conversation surface with role-appropriate visibility choices.

Additive table:

- `esp_support_messages`
  - `id`, `case_id`, `author_user_id`, `author_role`, `visibility`, `body`, `created_at`;
  - visibility is constrained to `user_visible` or `internal`;
  - foreign keys preserve case/user ownership.

Audit actions:

- `chat9.support_message_added`
- `chat9.support_internal_note_added`

The audit event deliberately records IDs/visibility but not message body contents.

## Versioned Training Academy contract now implemented

`esp_training_academy.py` adds a durable, version-bound Academy layer while retaining existing legacy progress modules for compatibility.

Member routes:

- `GET /command-center/api/training/courses`
- `GET /command-center/api/training/courses/{course_id}`
- `POST /command-center/api/training/courses/{course_id}/versions/{version}/lessons/{lesson_id}/complete`
- `POST /command-center/api/training/courses/{course_id}/versions/{version}/exam`
- `GET /command-center/api/training/courses/{course_id}/versions/{version}/progress`
- `GET /command-center/training`

Owner administration routes:

- `POST /command-center/api/training/owner/courses`
- `POST /command-center/api/training/owner/courses/{course_id}/versions`
- `PUT /command-center/api/training/owner/courses/{course_id}/versions/{version}`
- `POST /command-center/api/training/owner/courses/{course_id}/versions/{version}/publish`

Training rules:

- course identity is stable across versions;
- draft versions may be edited with optimistic `expected_revision` checks;
- published versions are immutable;
- editing a published course requires a new draft version;
- publishing is a consequential Owner action and requires explicit `confirm_publish=true`;
- role/region/niche audience scopes are server checked;
- member course payloads omit `correct_options` answer keys;
- lesson progress, exam attempts and certificates retain exact `course_id + version` provenance;
- publishing version N+1 does not erase or reinterpret version N completion/certificates;
- exam scoring is server-side;
- certificate issuance is idempotent per user/course/version.

Additive tables:

- `esp_training_courses_v2`
- `esp_training_course_versions_v2`
- `esp_training_lesson_progress_v2`
- `esp_training_exam_attempts_v2`
- `esp_training_certificates_v2`

Audit actions:

- `chat9.training_course_created`
- `chat9.training_version_created`
- `chat9.training_draft_updated`
- `chat9.training_version_published`

## Remaining Training/Support gaps after this addendum

The current branch still does not claim full completion of every Training/Support feature in the master specification. Remaining integration work includes richer practical-assignment/mentor-review workflows, configurable prerequisites/refreshers, certificate presentation/export, course retirement administration UI, support SLA policy configuration, assignment/escalation UI beyond the existing Owner triage fields, attachment storage integration with the canonical asset scanner, and broader notification delivery handoffs.
