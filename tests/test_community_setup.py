from content_workbench.bootstrap_windows import configure
from content_workbench.config import Settings, data_dir
from content_workbench import article_images

def test_first_run_is_blank_and_external_automation_is_disabled():
    settings = configure()
    assert settings.workspace_label == '内容工作台'
    assert settings.llm_backend == 'disabled'
    assert not settings.feishu_auto_archive and not settings.feishu_auto_sync
    assert not settings.model_connector_path and not settings.manual_path
    assert not settings.voice_guide_path and not settings.persona
    assert settings.login_mode == 'local'
    assert article_images.references() == []

def test_setup_preserves_existing_settings():
    Settings(author='用户自己的署名', workspace_label='用户自己的名称').save()
    assert configure().author == '用户自己的署名'
    assert configure().workspace_label == '用户自己的名称'

def test_references_only_use_current_installation_private_folder():
    folder = data_dir() / 'article-images' / 'references'
    folder.mkdir(parents=True)
    picture = folder / 'own-style.png'
    picture.write_bytes(b'test-reference')
    (folder / 'ignore.txt').write_text('not an image')
    assert article_images.references() == [picture]
