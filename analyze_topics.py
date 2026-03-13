#!/usr/bin/env python3
"""Аналіз конкретних топіків InSilver з channel_data.json."""

import json
import os
import sys
sys.path.append('../insilver-v2')
from group_analyzer import JewelryPatternAnalyzer

# Топіки для аналізу
TOPICS_OF_INTEREST = {
    2297: "Ексклюзивні прикраси",
    1802: "🔗Браслети", 
    1795: "⛓️Ланцюжки",
    2525: "💍Персні та Печатки",
    1806: "☦️Хрестики", 
    4205: "Кулон",
    7601: "Жіночі прикраси",
    7243: "В НАЯВНОСТІ",
    2131: "Набір для чоловіка",
    2224: "👼Ладанки", 
    3186: "Гравірування",
    2822: "⛓️Ланцюг +🔗Браслет",
    3250: "Напайки"
}

def load_channel_data():
    """Завантажує дані з channel_data.json."""
    if not os.path.exists("channel_data.json"):
        print("❌ Файл channel_data.json не знайдено")
        print("🚀 Спочатку запусти: python3 scrape_channel.py")
        return None
        
    with open("channel_data.json", "r", encoding="utf-8") as f:
        return json.load(f)

def filter_by_topics(posts):
    """Фільтрує пости по потрібних топіках."""
    topic_posts = {}
    other_posts = []
    
    for post in posts:
        topic_id = post.get('topic_id')
        
        if topic_id and topic_id in TOPICS_OF_INTEREST:
            topic_name = TOPICS_OF_INTEREST[topic_id]
            if topic_id not in topic_posts:
                topic_posts[topic_id] = {
                    'name': topic_name,
                    'posts': []
                }
            topic_posts[topic_id]['posts'].append(post)
        else:
            other_posts.append(post)
    
    return topic_posts, other_posts

def analyze_topics():
    """Аналізує топіки InSilver."""
    print("🔍 АНАЛІЗ ТОПІКІВ INSILVER")
    print("=" * 50)
    
    # Завантажуємо дані
    posts = load_channel_data()
    if not posts:
        return
        
    print(f"📊 Всього постів: {len(posts)}")
    
    # Фільтруємо по топіках
    topic_posts, other_posts = filter_by_topics(posts)
    
    print(f"🎯 В цільових топіках: {sum(len(t['posts']) for t in topic_posts.values())}")
    print(f"📝 Інші пости: {len(other_posts)}")
    
    # Статистика по топіках
    print(f"\n📋 РОЗБИВКА ПО ТОПІКАХ:")
    for topic_id, data in topic_posts.items():
        posts_count = len(data['posts'])
        with_text = len([p for p in data['posts'] if p.get('text')])
        with_photo = len([p for p in data['posts'] if p.get('photo')])
        print(f"   {data['name']}: {posts_count} постів ({with_text} з текстом, {with_photo} з фото)")
    
    # Підготовка даних для аналізатора термінів
    analyzer_messages = []
    topic_mapping = {}
    
    for topic_id, data in topic_posts.items():
        for post in data['posts']:
            if post.get('text') and post['text'].strip():
                message = {
                    'id': post['id'],
                    'date': post.get('date'),
                    'text': post['text'],
                    'photo': {'file_id': f"topic_{post['id']}"} if post.get('photo') else None,
                    'topic': data['name']
                }
                analyzer_messages.append(message)
                topic_mapping[post['id']] = data['name']
    
    print(f"\n🧠 Аналізую {len(analyzer_messages)} постів з текстом...")
    
    # Аналіз термінів
    analyzer = JewelryPatternAnalyzer()
    analyzer.process_messages(analyzer_messages)
    
    # Результати
    analyzer.print_summary()
    
    # Додаткова статистика по топіках
    print(f"\n🏆 ТЕРМІНИ ПО ТОПІКАХ:")
    topic_term_stats = {}
    
    for item in analyzer.classified_items:
        msg_id = item.get('message_id') or item.get('id')
        topic = topic_mapping.get(msg_id, 'Unknown')
        
        if topic not in topic_term_stats:
            topic_term_stats[topic] = {'count': 0, 'terms': []}
            
        topic_term_stats[topic]['count'] += 1
        if 'analysis' in item:
            for key, value in item['analysis'].items():
                topic_term_stats[topic]['terms'].append(f"{key}:{value}")
    
    for topic, stats in topic_term_stats.items():
        if stats['count'] > 0:
            terms_preview = ', '.join(stats['terms'][:3])
            print(f"   📁 {topic}: {stats['count']} термінів ({terms_preview}...)")
    
    # Збереження результатів
    analyzer.save_results('../insilver-v2/topics_analysis.json')
    verification_file = analyzer.save_verification_file('../insilver-v2/topics_verification.json')
    
    # Копіюємо як основний файл
    import shutil
    shutil.copy(verification_file, '../insilver-v2/verification_needed.json')
    
    print(f"\n✅ ГОТОВО!")
    print(f"📋 Файл верифікації: {verification_file}")
    print(f"📤 Скопійовано в ../insilver-v2/verification_needed.json")
    print(f"🎯 Владислав може тепер запустити /verify в боті!")
    
    # Показуємо приклади найкращих знайдених термінів
    print(f"\n💎 НАЙКРАЩІ ПРИКЛАДИ:")
    best_examples = []
    
    for item in analyzer.classified_items:
        if len(item.get('analysis', {})) >= 2:  # Мінімум 2 характеристики
            text = item['text'][:100] + ("..." if len(item['text']) > 100 else "")
            analysis = item['analysis']
            best_examples.append((text, analysis))
            
    for i, (text, analysis) in enumerate(best_examples[:5]):
        print(f"{i+1}. {analysis}")
        print(f"   \"{text}\"")

if __name__ == "__main__":
    analyze_topics()