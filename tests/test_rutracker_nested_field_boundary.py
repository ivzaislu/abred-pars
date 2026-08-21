from bs4 import BeautifulSoup

from abred_catalog_pipeline.rutracker import parser as rutracker_parser


def _post(html: str):
    soup = BeautifulSoup(html, "html.parser")
    return soup.select_one(".post_body")


def test_nested_metadata_label_does_not_spill_into_author_fields():
    post = _post(
        """
        <div class="post_body">
          <span class="post-b">Фамилия автора</span>:
          <span>
            Мартин
            <span class="post-b">Имя автора</span>: Джордж
            <span class="post-b">Исполнитель</span>: Бояров Евгений (ЛИ)
            <span class="post-b">Общее время звучания</span>: 03:29:26
            <span class="post-b">Жанр</span>: Фэнтези
          </span>
        </div>
        """
    )
    assert post is not None
    post_text = post.get_text("\n", strip=True)

    assert rutracker_parser._post_field(post, ("Фамилия автора",)) == "Мартин"
    assert rutracker_parser._post_field(post, ("Имя автора",)) == "Джордж"
    assert rutracker_parser._topic_authors(post, post_text) == ["Мартин Джордж"]


def test_nested_bold_author_value_is_not_mistaken_for_a_field_label():
    post = _post(
        """
        <div class="post_body">
          <span class="post-b">Автор</span>:
          <span><span class="post-b">Джордж Мартин</span></span><br/>
        </div>
        """
    )
    assert post is not None
    assert rutracker_parser._post_field(post, ("Автор",)) == "Джордж Мартин"


def test_nested_boundary_preserves_wbr_zero_width_behavior():
    post = _post(
        """
        <div class="post_body">
          <span class="post-b">Фамилия автора</span>:
          <span>Мар<wbr/>тин <span class="post-b">Имя автора</span>: Джордж</span>
        </div>
        """
    )
    assert post is not None
    assert rutracker_parser._post_field(post, ("Фамилия автора",)) == "Мартин"
