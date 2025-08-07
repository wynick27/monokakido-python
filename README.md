# Python版monokakido词典读取

monokakido词典文件主要包括nrsc，rsc，keystore和headlinestore四种。 
nrsc格式：词典的音频图像数据等  
rsc格式：词典本体数据  
keystore：索引数据  
headlinestore：标题数据  

## NSRC文件

由多个文件组成，index.nidx是索引，这里记录每个文件都在什么位置。有数字编号的.nrsc存了具体文件内容。知道文件序号和文件内偏移以及大小就可以到这个文件中查找对应的数据了。
下面是nidx的格式，用010 Editor的Binary Template脚本表示：

```
int zero; //总为0
int file_count;

struct {
    short compression;  //0 不压缩 1 zlib压缩
    short fileseq;  //文件序号
    int id_str_offset;  //文件名地址
    int file_offset;  //文件偏移
    int length;  //文件长度
} NrscIdxRecord[file_count] <read=ReadString(id_str_offset)>;
```
每条记录对应的文件名在索引结束后单独存放，通过偏移来查找。

## RSC文件

这些文件是由.idx索引文件，.map映射文件和数字编号的.rsc文件组成
contents.idx，这个文件是索引，从id到map中的条目，因为map也是顺序排列的，实际用处不大。
```
int count;
struct {
    int id;
    int map_index;
} idx_record[count];
```
contents.map，这个文件是记录条目的位置的，每个条目包括所在压缩块的偏移和压缩块内的偏移。
```
int count;
struct {
    int zoffset;  //压缩块偏移
    int ioffset;  //压缩块解压后的文件偏移
} map_record[count];
```
要查找每个文件，要找到对应压缩块，这个偏移不是文件内偏移，而是全局偏移，所以需要根据这个确定在哪个文件，然后就知道文件内的偏移了。
文件内的偏移是一个压缩块。首先是4字节压缩块长度，然后后面就是压缩数据了。
解压后的数据的偏移位置处也是先是4自己文件长度，然后才是实际数据。
``
## Headlinestore标题文件

这个多了很多数值为0的数据，实际和nrsc结构差不多，保存了每个条目的page_id和item_id，以及对应标题的文字。文字同样是在最后。
```
struct {
   int magic;  //总为 2
   int zero1;  //总为 0
   int rec_count;  //记录总数
   int rec_offset;  //开始位置
   int words_offset;  //字符串开始位置
   int rec_bytes;  //记录大小，总为24
   int magic4; //总为 0
   int magic5; //总为 0
} Header;

struct {
    int page_id;
    short item_id;
    short zero1; //总为 0
    int str_offset;
    int zero2; //总为 0
    int zero3; //总为 0
    int zero4; //总为 0
    
} HeadLine[Header.rec_count] <read=ReadWString(str_offset+Header.words_offset)>;
```
## Keystore索引文件

这个文件格式非常复杂，首先是文件头，然后是一个偏移数组，指向各单词条目。单词条目包括单词本身和对应的page_id和item_id列表。 page_id对应一页，item_id对应页内的内容。这个列表是变长的，在单词条目后面集中存储。

这个列表后面是索引文件头，可以通过文件头对应的idx_offset访问。 索引文件头里面有4个索引的偏移，注意这个偏移是相对索引文件头的。

在文件中，如果没有索引，对应条目为0，索引本身是一个偏移数组，就是按顺序排列的单词，可以方便二分查找。

这4个索引，决定不同的查询方式，
第一个索引是先按单词长度排序，再按字典顺序排序，这个索引可以查某一长度的单词范围，用处不大。
第二个索引是正常的字典序，可以查询指定内容开头的词。
第三个索引是按后缀排序的，也就是先把单词反转再排序。
第四个索引是按单词中出现的字排序的，也就是拆散每个单词，然后按字排序后重组。这个可以用于查询任意顺序查询出现的字，有一定用处但作用不大。
```
struct {
    int ver;
    int magic1;
    int words_offset;
    int idx_offset;
    int next_offset;
    int magic5;
    int magic6;
    int magic7;
} Header;

FSeek(Header.words_offset);
int count;
int word_offsets[count];
local int i;
for (i = 0; i < count; i++)
{
    FSeek(word_offsets[i]+Header.words_offset);
    struct {
    int offset;  //索引对应的page和item列表的偏移
    byte zero;  //总为 0
    char text[]; //单词本身
    
    } word_entry <read=(text)>;
}
typedef struct  {
    byte type:4;
    byte item_len:4;
    switch (type)
    {
        case 1:
        byte page_id;
        break;
        case 2:
        byte page_id[2];
        break;
        case 4:
        byte page_id[3];
        break;
    }
    if (item_len == 1)
        byte item_id;
    else if (item_len == 2)
        ushort item_id;

} page_id_t <read=Str("page:%d item:%d",
(type == 1 ? page_id : (type == 2 ? ((uint)page_id[0] << 8) + page_id[1] : 
((uint)page_id[0]<< 16) + ((uint)page_id[1]<< 8)+ page_id[2])),
(item_len != 0 ? item_id : 0))>;
for (i = 0; i < count; i++)
{
    FSeek(word_entry[i].offset + Header.words_offset);
    struct {
    short count;
    local int j;
    for (j = 0;j<count;j++)
    {
        page_id_t page_id;
    }
    
    } pages;
}

FSeek(Header.idx_offset);
struct {
    int count;
    int index_a_offset;
    int index_b_offset;
    int index_c_offset;
    int index_d_offset;
} Index;
int count_a;
int index_a[count_a];
int count_b;
int index_b[count_b];
int count_c;
int index_c[count_c];
int count_d;
int index_d[count_d];
```
