import sqlite3
db_path = r'D:\Tiger\Wenqu v1.1\wenqu_data\wenqu.db'
conn = sqlite3.connect(db_path)
c = conn.cursor()
print('Total chapters:', c.execute("SELECT COUNT(*) FROM chapters WHERE course_id=?", ("bbd89075",)).fetchone()[0])
print('is_loaded=1:', c.execute("SELECT COUNT(*) FROM chapters WHERE course_id=? AND is_loaded=1", ("bbd89075",)).fetchone()[0])
print('is_loaded=0:', c.execute("SELECT COUNT(*) FROM chapters WHERE course_id=? AND is_loaded=0", ("bbd89075",)).fetchone()[0])
print('Syllabus items:', c.execute("SELECT COUNT(*) FROM syllabus_items WHERE course_id=?", ("bbd89075",)).fetchone()[0])
print('Snapshots:', c.execute("SELECT COUNT(*) FROM chapter_snapshots WHERE course_id=?", ("bbd89075",)).fetchone()[0])
print()
print('First 5 chapters:')
for r in c.execute("SELECT idx, substr(title,1,40), is_loaded, length(coalesce(content_slice,'')) FROM chapters WHERE course_id=? ORDER BY sort_order LIMIT 5", ("bbd89075",)):
    print(' ', r)
conn.close()