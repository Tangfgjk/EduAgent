"""One frozen pending course for runtime bank, catalogue and measurements."""
from app.core.schema import QuestionItem
from app.learning.course_draft import build_equation_course_draft


def freeze_course():
    package = build_equation_course_draft()
    reviews = {review.assessment_ref.key: review for review in package.task_reviews}
    bank = []
    # Normal tutoring uses practice assets only. Distinct measurement assets are
    # kept exclusively in the catalogue for pre/post/transfer/delayed evaluation.
    for asset in sorted(package.catalog.assessments, key=lambda item: item.ref.key):
        if asset.kind != 'practice':
            continue
        review = reviews[asset.ref.key]
        hints = [dict(level=step.level, form=('nudge', 'directive', 'worked_partial', 'worked_full')[step.level],
                      text=step.text) for step in review.hints]
        bank.append(QuestionItem(item_id=asset.ref.asset_id, kc_id=asset.kc_refs[0].asset_id,
            stem=asset.stem, answer=asset.answer, difficulty=.2, taxonomy_level=asset.bloom_level,
            pattern='practice', hints=hints, misconception_links=[]))
    if not bank:
        raise ValueError('frozen_course_has_no_practice_items')
    return package, bank
