from aura_music_studio.video_storyboard import MusicVideoStoryboardPlanner, SongSection


def test_storyboard_preserves_sections_and_timing():
    planner = MusicVideoStoryboardPlanner()
    shots = planner.build(
        title="Sparkles & Glistens",
        visual_concept="luminous cinematic performance with elegant celestial imagery",
        aspect_ratio="9:16",
        sections=[
            SongSection("verse 1", 0, 24, 0.35, "There's a lady I know"),
            SongSection("chorus", 24, 48, 0.85, "she sparkles and glistens"),
        ],
    )
    assert len(shots) == 2
    assert shots[0].start_seconds == 0
    assert shots[1].end_seconds == 48
    assert "9:16" in shots[0].prompt
    assert "Sparkles & Glistens" in shots[1].prompt
    assert shots[1].transition == "beat-aligned cut"
