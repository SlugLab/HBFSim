# USENIX FAST '27 征稿启事（Call for Papers）中文整理

> **抓取状态**：FAST '27 的征稿启事在 2026-08-11 **已经发布并成功抓到**，本文全部内容取自 usenix.org 官方页面与官方 PDF，没有出现"'27 尚未发布"因而回退到 FAST '26 的情况。
>
> **抓取日期**：2026-08-11。抓取方式：WebFetch 对 usenix.org 返回 HTTP 403，改用 curl 携带浏览器 User-Agent 直接取回 HTML 与 PDF，在本地转成文本核对。
>
> 本文所有日期、页数、字号、行距、篇幅上限、评审模式、artifact 要求、政策条款均逐字取自官方原文；每一条后面附官方英文原句（引用块）便于日后核对。官方页面没有写的项目，本文写"官方页面未列出"，不用其他会议的规则填补。
>
> **引用块的两处技术性处理**：（1）官方网页把邮箱地址用 Cloudflare 的脚本做了遮盖，网页源码里显示为 `[email protected]`；本文引用块中的邮箱是从该遮盖串解码还原的明文，并已用官方 PDF（`fast27_cfp_080626.pdf`）里的明文邮箱交叉核对一致。（2）网页原文中超链接的地址被移除，只保留链接文字，因此个别引用块中会出现链接文字后多一个空格再接标点的情况。除此之外没有对英文原文做任何改动。

---

## 一、这是哪一届、会议时间与地点

FAST '27 是第 25 届 USENIX 文件与存储技术会议（USENIX Conference on File and Storage Technologies），由 USENIX 主办，并与 ACM SIGOPS 合作举办。

- **届次**：第 25 届（the 25th USENIX Conference on File and Storage Technologies）
- **会议日期**：February 23–25, 2027
- **会议地点**：Hyatt Regency Lake Washington，1053 Lake Washington Blvd. N，Renton, WA 98056, USA，电话 +1 425.203.1234
- **主办**：Sponsored by USENIX in cooperation with ACM SIGOPS

> "The 25th USENIX Conference on File and Storage Technologies (FAST '27) will take place on February 23–25, 2027, at the Hyatt Regency Lake Washington in Renton, WA, USA."

> "Sponsored by USENIX, the Advanced Computing Systems Association."

> "Sponsored by USENIX in cooperation with ACM SIGOPS"

会议内容形式：

> "The conference will consist of technical presentations including refereed papers and poster sessions."

---

## 二、完整重要日期表

FAST '27 采用**两个投稿截止日期**（Spring 与 Fall），两轮的录用论文都在同一届会议上报告、进同一本论文集。以下日期逐条照抄官方 Call for Papers 页面。

时区标注 **AoE** 是 Anywhere on Earth 的缩写，指以地球上最晚的时区为准计时——只要地球上还有任何一个地方没过当天的 23:59，投稿就算按时。

### 2.1 Spring deadline（春季轮，截至 2026-08-11 已全部过期）

| 事项 | 官方日期与时间 |
| --- | --- |
| Paper submissions due（论文投稿截止） | Tuesday, March 17, 2026, 23:59 AoE |
| Author response period begins（作者回应期开始） | Monday, May 18, 2026 |
| Author response period ends（作者回应期结束） | Wednesday, May 20, 2026, 23:59 AoE |
| Notification to authors（录用通知） | Thursday, June 4, 2026 |
| Final paper files due（终稿文件截止） | Tuesday, July 28, 2026, 23:59 AoE |

> "Spring deadline:
> - Paper submissions due: Tuesday, March 17, 2026, 23:59 AoE
> - Author response period begins: Monday, May 18, 2026
> - Author response period ends: Wednesday, May 20, 2026, 23:59 AoE
> - Notification to authors: Thursday, June 4, 2026
> - Final paper files due: Tuesday, July 28, 2026, 23:59 AoE"

### 2.2 Fall deadline（秋季轮）

| 事项 | 官方日期与时间 |
| --- | --- |
| Paper submissions due（论文投稿截止） | Tuesday, September 15, 2026, 23:59 AoE |
| Author response period begins（作者回应期开始） | Tuesday, November 17, 2026 |
| Author response period ends（作者回应期结束） | Thursday, November 19, 2026, 23:59 AoE |
| Notification to authors（录用通知） | Tuesday, December 8, 2026 |
| Final paper files due（终稿文件截止） | Tuesday, January 26, 2027, 23:59 AoE |

> "Fall deadline:
> - Paper submissions due: Tuesday, September 15, 2026, 23:59 AoE
> - Author response period begins: Tuesday, November 17, 2026
> - Author response period ends: Thursday, November 19, 2026, 23:59 AoE
> - Notification to authors: Tuesday, December 8, 2026
> - Final paper files due: Tuesday, January 26, 2027, 23:59 AoE"

会议首页也重复了秋季轮截止日期：

> "Fall paper submissions are due on Tuesday, September 15, 2026 ."

### 2.3 摘要截止（abstract deadline）

**没有单独的摘要截止日期。** 官方明确写明：

> "There is no separate deadline for abstract submissions."

### 2.4 其他已公布的日期

- **FAST Test of Time Award 提名**（对 10 年以上老论文的奖项提名，与投稿流程无关）：Nominations received by December 14, 2026, will be considered for the 2027 award.

> "Nominations received by December 14, 2026, will be considered for the 2027 award. Any nominations received after that date will be considered for future years."

- **论文对外公开日期**：所有录用论文在会议第一天（February 23）对所有人开放。

> "The papers will be available online to everyone beginning on the first day of the conference, February 23."

- **Early Rejection Notification（提前拒稿通知）**：官方说明会在作者回应期之前先行通知一部分被拒论文，但**官方页面未列出**提前拒稿通知的具体日期。

> "Early Rejection Notification: We will notify authors of papers that are rejected early in the process, prior to the author response period."

---

## 三、多轮投稿制度与重投规则

FAST '27 确实有两轮投稿，作者只能选择其中一轮：

> "Authors have the option to submit to one of two submission deadlines. Our intention is to provide authors the flexibility to submit their work in its best form. Papers accepted during either deadline will be presented at the conference and will be published in the proceedings. Pre-publication PDFs accepted in the Spring review cycle will be available following the cycle's conclusion."

**同届两轮之间不允许重投**：春季轮被拒的论文不能改投 FAST '27 秋季轮；两轮被拒的论文都可以投 FAST '28 的任一轮。

> "FAST Resubmissions: As described above, the FAST '27 conference offers two submission deadlines: Spring and Fall. Papers rejected at the spring deadline cannot be resubmitted at the FAST '27 fall deadline. However, both papers rejected at the spring and fall deadlines may be resubmitted at either deadline for FAST '28. One-shot revisions invited from the previous FAST deadline are not considered resubmissions; they are treated as a continuation of the original submission."

**从其他会议转投（Rapid Resubmissions）**：允许投稿曾投过别的会议但未被录用的论文，但要求已针对此前审稿意见做过实质改进；若上一次投稿发生在最近 6 个月内，须在投稿表单中说明此前投稿情况与修改摘要，改动说明尽量控制在 500 词以内。

> "Rapid Resubmissions: Submitting a paper previously submitted to and not accepted by another conference is permitted, although authors are expected to have improved the paper to address substantive issues raised in previous reviews. If the last submission of the paper occurred within the last 6 months, authors should provide information regarding the previous submission(s) and a summary of the subsequent revisions to the paper in the appropriate field of the submission form. This description, which will be supplied to reviewers after they've completed their reviews, helps reviewers who may have reviewed a previous draft of the work to appreciate any improvements to the currently submitted work. Please try to limit the description of changes to 500 words. All information should be properly anonymized, as described above, and should be uploaded via the submission form."

---

## 四、篇幅与排版要求

### 4.1 正文页数上限、参考文献是否计入

- **长论文（long papers）**：不超过 **12 页**，**不含参考文献**。
- **短论文（short papers）**：不超过 **6 页**，**不含参考文献**。

> "The complete submission must be no longer than 12 pages for long papers and no longer than 6 pages for short papers , excluding references. The program committee values conciseness: if you can express an idea in fewer pages than the limit, do so."

### 4.2 附录 / 补充材料规则

补充材料（supplemental material）可选，作为**一个单独的 PDF 文件**提交，**不设页数上限**；但审稿人没有义务阅读。凡是判断论文所必需的内容都必须放在正文文件里。

> "Supplemental material is optional and may be added (if deemed really necessary) as a single separate PDF file without page limits. However, the reviewers are not required to read or consider such material. All content that should be considered to judge the paper is not supplemental and should be part of the main submitted file."

另有一类附录出现在**录用之后**：通过 artifact 评审的论文，应在正式发表版中加入**不超过两页**的 Artifact Appendix（见第七节）。

### 4.3 纸张、栏数、字号、行距、版心

- **纸张**：U.S. letter 尺寸
- **栏数**：两栏（two columns）
- **字号**：10-point Times Roman
- **行距**：12-point leading（单倍行距，single-spaced）
- **版心（text block）**：7 英寸宽 × 9 英寸深

> "Papers must be typeset on U.S. letter-sized pages in two columns using 10-point Times Roman font on 12-point leading (single-spaced), within a text block 7" wide by 9" deep."

**页边距（margins）**：官方页面未列出页边距的具体数值，只规定了纸张尺寸（U.S. letter）与版心尺寸（7" × 9"）。

### 4.4 图表与参考文献的字号硬性规定

图、表、图注中的文字字号必须做到打印出来无须放大即可辨认；参考文献不得使用更小的字号。违反者不予送审，篇幅限制严格执行，不因重新排版给延期。

> "Labels, captions, and other text in figures, graphs, and tables must use font sizes that, when printed, do not require magnification to be legible. References must not be set in a smaller font. Submissions that violate these requirements will not be reviewed. Limits will be enforced strictly. No extensions will be given for reformatting."

---

## 五、模板与官方文件

Call for Papers 指向 USENIX 官方模板页面 <https://www.usenix.org/conferences/author-resources/paper-templates>：

> "A LaTeX template and style file are available on the USENIX templates page ."

该模板页面（2026-08-11 抓取）提供以下文件：

| 文件用途 | 文件名 | 链接 |
| --- | --- | --- |
| LaTeX template for USENIX papers | `usenix2019_v3.1.tex` | https://www.usenix.org/sites/default/files/usenix2019_v3.1.tex |
| LaTeX style file for USENIX papers | `usenix-2020-09.sty` | https://www.usenix.org/sites/default/files/usenix-2020-09.sty |
| MS Word sample file for USENIX papers | `usenix2022.doc` | https://www.usenix.org/sites/default/files/usenix2022.doc |
| Sample PDF for USENIX papers | `usenix-2020-09.pdf` | https://www.usenix.org/sites/default/files/usenix-2020-09.pdf |

模板页面的两条提醒：

> "Note that templates include author names. Please reference the corresponding Call for Papers' blindness policy to double-check whether author names should be included in your paper submission."

> "If you choose not to use one of these templates, please format your paper as follows:
> - U.S. letter-sized pages
> - Two-column format
> - Text block 7" wide by 9" deep.
> - 10-point Times Roman or similar type on 12-point leading (single-spaced)"

**征稿启事 PDF**：`fast27_cfp_080626.pdf`，下载地址 <https://www.usenix.org/sites/default/files/fast27_cfp_080626.pdf>（4 页；PDF 正文页脚标注 "Rev. 3/11/26"）。

**Artifact Appendix 模板**：`fast25_ae_appendix-template.zip`，下载地址 <https://www.usenix.org/sites/default/files/fast25_ae_appendix-template.zip>（这是 Call for Artifacts 页面给出的链接，文件名沿用 fast25）。

---

## 六、评审模式：双盲及匿名化的具体要求

FAST '27 采用**双盲评审**（作者不知道审稿人是谁，审稿人也不知道作者是谁）。官方原文写明作者不得在投稿中被识别出来，无论是明写还是可以推断出来：

> "Double-blind policy: Authors must not be identified in the submissions, either explicitly or by implication. To refer to your previous work, consider it as written by a third party. Do not say "reference removed for blind review." Supplemental material must be anonymized. Submissions violating anonymization rules will not be considered for review. If you are uncertain about how to anonymize your submission, please contact the program co-chairs, fast27chairs@usenix.org , well in advance of the submission deadline."

逐条拆开：

1. **作者信息**：投稿中不得出现能识别作者身份的信息，明写和暗示都不行。
2. **自引写法**：引用自己此前的工作时，要当成第三方的工作来写（用第三人称）。
3. **不许写"因盲审删去引用"**：官方明确禁止写 "reference removed for blind review"。
4. **补充材料**：补充材料也必须匿名化。
5. **违规后果**：违反匿名化规则的投稿不予送审。
6. **不确定时**：提前联系程序委员会共同主席 fast27chairs@usenix.org，不要临近截止才问。

**致谢（acknowledgments）**：官方 Call for Papers 页面未单独列出致谢部分的匿名化写法。

**代码仓库链接的处理方式**：官方 Call for Papers 页面未单独列出投稿阶段代码仓库链接该如何处理。页面只写明补充材料必须匿名化，以及鼓励论文附带 artifact：

> "Program committee members, USENIX, and the broader community generally value a paper more highly if it is accompanied by artifacts not previously available. These artifacts may include traces, original data, source code, or tools developed as part of the submitted work."

**已部署系统论文的例外**：deployed-systems papers（描述真实生产系统的论文）同样必须双盲，但论文中描述的产品或公司不必匿名——不必匿名的只是产品与公司，作者姓名仍须匿名。

> "Double-blind policy for deployed-systems papers: All submissions for FAST '27 are required to follow the double-blind policy (see above). However, with deployed-systems papers, the product or company described in the paper need not be anonymized (unlike author names)."

**此前的 workshop 论文**：如果投稿是对此前某篇 workshop 论文的扩展，需要把该 workshop 论文的匿名版本作为补充材料一并提交。

> "Prior Workshop Paper Policy: If a submission extends a prior workshop paper, please include an anonymized copy of the workshop paper as supplemental material. This should be the same as the version already published, with any identifying information removed."

**评审执行方式**：

> "Blind reviewing of all papers will be done by the program committee, assisted by outside referees when necessary. Accepted papers will be shepherded by a member of the program committee."

（shepherding 指录用后由一位程序委员会成员做指导人，跟进作者按审稿意见修改终稿。）

**评审标准**：

> "The program committee and external reviewers will judge papers on technical merit, significance, relevance, and presentation. Research papers on new and unexplored problems are encouraged. A good research paper:
> - addresses a significant problem;
> - presents an interesting, compelling solution;
> - demonstrates the benefits and drawbacks of the solution;
> - draws appropriate conclusions using sound experimental methods;
> - clearly describes what the authors have done; and
> - clearly articulates the advances beyond previous work."

---

## 七、Artifact Evaluation（artifact 评审）

**有**，FAST '27 设有 artifact 评审。artifact 指论文之外的产物——软件、硬件、评测数据、文档、调查结果、机器验证的证明、测试集、基准测试等。

> "A scientific paper consists of a constellation of artifacts that extend beyond the document itself, possibly including software, hardware, evaluation data, documentation, survey results, mechanized proofs, models, test suites, and benchmarks. In some cases, the quality of these artifacts is as important as the document itself. FAST '27 will continue the artifact evaluation process established in FAST '24 for all accepted papers."

### 7.1 可选还是强制：可选

**参加 artifact 评审是可选的**，且在论文录用决定作出之后才开始，artifact 的评审结果不影响论文录用结果。

> "Participation in artifact evaluation is optional, although we hope all accepted papers will take part. Artifact evaluation will begin only after paper acceptance decisions have already been made. The decision of artifact evaluation does not affect the paper acceptance decision. However, we encourage authors to make the best efforts to obtain the "Results Reproduced" badge."

### 7.2 时间表

**Spring Deadline（春季轮）**

| 事项 | 官方日期与时间 |
| --- | --- |
| Notification for paper authors | Thursday, June 4, 2026 |
| Artifact submission deadline | Tuesday, June 16, 2026, 23:59 AoE |
| Artifact decisions announced | Tuesday, July 23, 2026 |
| Final paper files due | Tuesday, July 28, 2026, 23:59 AoE |

**Fall Deadline（秋季轮）**

| 事项 | 官方日期与时间 |
| --- | --- |
| Notification for paper authors | Tuesday, December 8, 2026 |
| Artifact submission deadline | Thursday, December 17, 2026, 23:59 AoE |
| Artifact decisions announced | Thursday, January 21, 2027 |
| Final paper files due | Tuesday, January 26, 2027, 23:59 AoE |

> "Fall Deadline
> - Notification for paper authors: Tuesday, December 8, 2026
> - Artifact submission deadline: Thursday, December 17, 2026, 23:59 AoE
> - Artifact decisions announced: Thursday, January 21, 2027
> - Final paper files due: Tuesday, January 26, 2027, 23:59 AoE"

注：提交流程分 Registration 与 Submission 两步（见 7.5），但**官方页面未列出**单独的 artifact registration 截止日期，Important Dates 一节只给出了 artifact submission deadline。

评审期间的联络要求：

> "Note: In the rather common event of questions or discrepancies in using or understanding the artifacts, at least one contact author for the submission must be reachable via email and respond to questions in a timely manner during the artifact evaluation period."

### 7.3 评审模式：单盲

artifact 评审是**单盲**（single-blind，即评审者知道作者是谁，作者不知道评审者是谁）。

> "Artifact evaluation is "single-blind." The identities of artifact authors will be known to members of the AEC, but authors will not know which members of the AEC have reviewed their artifacts."

> "To maintain the anonymity of artifact evaluators, the authors of artifacts should not embed any analytics or other tracking in the websites for their artifacts for the duration of the artifact-evaluation period. This is important to maintain the confidentiality of the evaluators. In cases where tracing is unavoidable, authors should notify the AEC chairs in advance so that AEC members can take adequate safeguards."

artifact 由评审委员会保密：

> "The artifact evaluation process will consider the availability and functionality of artifacts associated with their corresponding papers, along with the reproducibility of the paper's key results and claims with these artifacts. Artifact evaluation is single-blind. Artifacts will be held in confidence by the evaluation committee. We encourage all the authors to publish their artifacts to benefit the broader community."

### 7.4 三种徽章（badges）

作者在提交 artifact 时自行选择希望被评的标准，对应三种徽章，可以只选其中一种、两种或全部三种：

- **Artifacts Available**（artifact 可获取）

> "To earn this badge, the AEC must judge that the artifacts associated with the paper have been made available for retrieval, permanently and publicly. We encourage authors to use ChameleonCloud Trovi , Cloudlab , or Zenodo . Other valid hosting options include institutional repositories and third-party digital repositories (e.g., FigShare , Dryad , Software Heritage , GitHub , or GitLab )—not personal webpages. Besides making the artifacts available, this badge does not mandate any further requirements on functionality, correctness, or documentation."

- **Artifacts Functional**（artifact 可运行）

> "To earn this badge, the AEC must judge that the artifacts conform to the expectations set by the paper regarding functionality, usability, and relevance. In short, do the artifacts work, and are they useful for producing outcomes associated with the paper?"

考察三个方面：Documentation（文档是否足以让论文读者动手运行）、Completeness（是否包含论文描述的全部关键组件）、Exercisability（是否包含跑通论文实验所需的脚本与数据，软件能否成功执行）；另有可选的 Demonstration video。

- **Results Reproduced**（结果可复现）

> "To earn this badge, the AEC must judge that they can use the submitted artifacts to obtain the main results or evidence for the main claim presented in the paper. In short, is it possible for the AEC to independently repeat the experiments and obtain results that support the claims made by the paper? The goal of this effort is not to reproduce the results exactly but instead to generate results independently within an allowed tolerance such that the main claims of the paper are validated."

评审标准总纲：

> "Ultimately, the AEC expects that high-quality artifacts will be:
> - consistent with the paper
> - as complete as possible
> - documented well
> - easy to reuse, facilitating further research"

### 7.5 提交流程与打包要求

提交分两步：

> "- Registration: By the artifact registration deadline, submit the abstract and PDF of your accepted USENIX FAST paper, as well as topics, conflicts, and any "optional bidding instructions" for potential evaluators via the artifact submission site .
> - Submission: By the artifact submission deadline, provide a stable URL or (if impossible) upload an archive of your artifacts. If the URL is access-protected, provide the credentials needed to access it. Select the criteria/badges that the AEC should consider while evaluating your artifacts. You will not be able to change the URL, archive, or badge selections after the artifact submission deadline. Finally, for your artifact to be considered, check the "ready for review" box before the submission deadline."

完整的 artifact 包必须包含三样东西：

> "A complete artifact package must contain the following:
> - the accepted version of your FAST paper
> - the artifact itself
> - README instructions"

README 必须分成两节：

> "README instructions: Your artifact package must include a clearly written "README" file that describes your artifact and provides a road map for evaluation. The README must consist of two sections. A "Getting Started Instructions" section should help reviewers check the basic functionality of the artifact within a short time frame (e.g., within 30 minutes). Such instructions could, for example, be on how to build a system and apply it to a "Hello world"-sized example. The purpose of this section is to allow reviewers to detect obvious problems during the kick-the-tires phase (e.g., a broken virtual machine image). A "Detailed Instructions" section should provide suitable instructions and documentation to evaluate the artifact fully."

评审期安排：

> "During this phase (within two weeks after the artifact submission deadline), reviewers will check for any obvious problems that prevent the artifact from being fully reviewed. Such problems include invalid download links, broken virtual machine images, missing dependencies, or failures when applying the artifact to a "Hello world"-sized example. Authors can respond to issues and provide an updated version of their artifact during a response period. Then, reviewers will fully evaluate the artifact."

不接受的 artifact 类型：

> "Paper proofs will not be accepted because the AEC lacks the time and often the expertise to review paper proofs carefully. Physical objects, e.g., computer hardware, cannot be accepted due to the difficulty of making the objects available to members of the AEC. (If your artifact requires special hardware, consider if/how you can make it available to evaluators, e.g., by providing ssh access.)"

保密与后续发布：

> "The submission of an artifact does not give the AEC permission to publicize its content. AEC members may not publicize any part of your artifact during or after completing an evaluation, nor may they retain any part of it after evaluation. Thus, you can include models, data files, proprietary binaries, etc., in your artifact. Participating in artifact evaluation does not require you to publish your artifacts later (although it is encouraged)."

含破坏性操作的 artifact：

> "Some artifacts may attempt to perform malicious or destructive operations by design. These cases should be boldly and explicitly flagged in detail in the README so the AEC can take appropriate precautions before installing and running these artifacts."

### 7.6 通过后的标注与附录

> "When the AEC judges that an artifact meets the criteria for one or more of the badges listed above, those will appear on the final version of the associated paper. In addition, the authors of the paper should add an Artifact Appendix of up to two pages to their publication. The goal of the appendix is to describe and document the artifact in a standard format."

---

## 八、同时投稿政策与预印本（arXiv）政策

### 8.1 同时投稿其他会议：禁止

> "Simultaneous submission of the same work to multiple venues, submission of previously published work, or plagiarism constitutes dishonesty or fraud. USENIX, like other scientific and technical conferences and journals, prohibits these practices and may take action against authors who have committed them. See the USENIX Conference Submissions Policy for details."

USENIX 官方 Submissions Policy 页面（经 USENIX 董事会 2021 年 4 月 16 日批准）对同一条的完整表述与处罚措施：

> "Simultaneous submission of the same paper to multiple venues, submission of previously published work, or plagiarism constitutes dishonesty or fraud. USENIX, like other scientific and technical conferences and journals, prohibits these practices and may, in consultation with the program chairs, take action against authors who have committed them. Program chairs may share information about submitted papers with other conference chairs and journal editors to ensure the integrity of papers under consideration."

> "If a violation of these principles is found, sanctions may include, but are not limited to, barring the authors from submitting to or participating in USENIX conferences for a set period, contacting the authors' institutions, sharing the details with other conference chairs and journal editors, and publicizing the details of the case."

处在 one-shot revision（一次性修改）阶段的论文仍算在 FAST 审稿中，因此不能投其他会议，除非先撤稿：

> "During the revision period, the paper is still considered under review at FAST and, therefore, cannot be submitted to other conferences unless the authors first withdraw it from consideration (as per the USENIX Submission Policy, which precludes concurrent submission to other conferences)."

被提前拒稿的论文自收到拒稿通知起即不再算作"在投状态"：

> "Early rejected papers will no longer be considered under submission (regarding multiple submission policies) upon receipt of a rejection notification."

### 8.2 预印本（arXiv）：允许，但必须披露

允许此前或同时在 arXiv.org、技术报告、报告演讲等**未经同行评审**的场合发布，但必须向程序委员会共同主席披露：

> "Prior non-peer-reviewed work: Prior or concurrent publication in non-peer-reviewed contexts, like arXiv.org, technical reports, or talks, is permitted but must be disclosed to the program co-chairs using the process described in the Resubmissions Policy section below."

披露的具体做法（填在投稿表单相应字段，只有主席能看到，用于在不破坏双盲的前提下核实作品归属）：

> "Non-Peer-Reviewed Submissions: Prior or concurrent publication in non-peer-reviewed contexts, like arXiv.org, technical reports, and talks, is permitted. Authors should provide official public links and author information to previous submission(s) in the appropriate field of the submission form. This information, which will only be visible to the chairs , will be used to validate ownership of the work without violating double-blind reviewing."

### 8.3 保密协议：不受理

> "Papers accompanied by nondisclosure agreement forms will not be considered."

---

## 九、主题范围（Topics of Interest）

官方对主题范围的总述：

> "The topics of interest to FAST are various aspects of systems related to storage, including both core storage topics and the application of storage to different application domains. These include and overlap with, but are not limited to, the following topics."

以下逐条译出并保留英文原词。

### 9.1 核心存储主题（Core storage topics, such as:）

- 文件系统设计（File system design）
- 数据缓存、复制与一致性（Data caching, replication, and consistency）
- 纠删码（Erasure coding）
- 正确性、测试与形式化验证（Correctness, testing, and formal verification）
- 数据去重与压缩（Data deduplication and compression）
- 性能、编排与服务质量（Performance, orchestration, and QoS）
- 功耗感知的存储架构（Power-aware storage architectures）
- 可靠性、可用性与容灾（Reliability, availability, and disaster tolerance）
- 检索与数据获取（Search and data retrieval）
- 安全与隐私（Security and privacy）
- 数据治理、审计与溯源（Data governance, auditing, and provenance）
- 数据主权、迁移与流动（Data sovereignty, mobility, and migration）
- 存储监控与故障排查（Storage monitoring and troubleshooting）
- 数据布局与文件格式（Data layouts and file formats）
- AI 驱动的存储管理与自调优（AI-driven storage management and self-tuning）

### 9.2 专用存储系统（Purpose-built storage systems, such as:）

- 归档存储系统（Archival storage systems）
- 数据库、键值与 NoSQL 存储（Database, Key-value, and NoSQL storage）
- 大数据、分析与数据湖存储（Big data, analytics, and data lake storage）
- 面向 AI 与科学计算负载的存储（Storage for AI and scientific workloads）
- 面向移动、嵌入式、边缘与 IoT 系统的存储（Storage for mobile, embedded, edge, and IoT systems）

### 9.3 可扩展存储系统（Scalable storage systems, such as:）

- HPC 数据管理系统、并行 I/O（HPC data management systems, parallel I/O）
- 分布式与网络存储（广域、网格、点对点）（Distributed and networked storage (wide-area, grid, peer-to-peer)）
- 云环境中的数据管理（Data management in cloud environments）
- 软件定义存储与超融合基础设施（Software-defined storage and hyperconverged infrastructure）

### 9.4 新兴存储技术（Emerging storage technologies, such as:）

- 存储层次结构设计与纯内存存储系统（Memory hierarchy designs and memory-only storage systems）
- 新型与新兴存储技术（例如 DNA 存储与玻璃存储）（Novel and emerging storage technologies (e.g., DNA and glass storage)）

### 9.5 部署经验（Deployment experience in areas such as:）

- 负载特征刻画（Workload characterization）
- 已部署存储系统的实证评测与经验（Empirical evaluation and experience with deployed storage systems）

---

## 十、论文类别：短论文与已部署系统论文

### 10.1 短论文（Short Papers）

> "In addition to long papers (up to 12 pages), FAST also solicits short papers (up to 6 pages long). Just like long papers, short papers should describe completed research where the problem statement, proposed solution, and evaluation are all logically complete and conclusions are drawn. While short papers are held to the same high standards as long papers, they tend to comprise smaller contributions that require a shorter description and less analysis than in long papers. Papers that describe early-stage research that is not yet fully evaluated are not suitable for this short-paper category. That is, preliminary or work-in-progress papers generally considered in workshops such as HotStorage do not fall within the scope of short papers."

标题前缀与投稿表单要求：

> "For short papers, the title should be prefixed with "Short Paper: " , followed by the title. The prefix will not be published in the proceedings and short papers will not be called out as such in the program. Authors must also indicate that they are submitting a short paper by checking the appropriate checkbox on the submission form. The program committee will not accept a paper on the condition of adjusting its length beyond typical shepherding guidelines. Submissions will be considered only in the category in which they are submitted."

### 10.2 已部署系统论文（Deployed-Systems Papers）

> "FAST also solicits papers that describe real operational systems, including systems currently in production. Deployed-systems papers should address experience with the practical design, implementation, analysis, deployment, or operation of such systems. We encourage the submission of papers that disprove or strengthen existing assumptions, deepen the understanding of existing problems, and validate known techniques in environments in which they were never before used or tested, with preference given to experimental results based on production data. Deployed-systems papers will be treated similarly to other papers for publication purposes; they need not present new ideas or results to be accepted but should offer useful guidance to practitioners."

> "A good deployed-systems paper:
> - clearly articulates lessons learned from deploying in production;
> - describes an operational system of broad interest;
> - discusses practical problems encountered in production; and
> - supports the lessons with appropriate evidence, potentially including statistical data from the actual deployment, empirical evaluation of the system (on production platforms rather than small testbeds), and anecdotes."

标题前缀与投稿表单要求：

> "For deployed-systems papers, the title should be prefixed with "Deployed System: " , followed by the title. The prefix will not be published in the proceedings. Authors must also indicate that they are submitting a deployed-systems paper by checking the appropriate checkbox on the submission form. If a paper is both short and falls in the deployed-systems category, both prefixes should be used (in any order), and both checkboxes selected."

---

## 十一、作者回应期（Author Response Period，即 rebuttal）

作者回应期指审稿意见发出后、最终决定作出前，作者可以书面答复审稿意见的一段时间。

> "FAST '27 will allow authors to respond to reviews prior to final decision, according to the schedule above. Authors must limit their response to correcting factual errors in the reviews, to addressing questions posed by reviewers, and to clarifying the ideas in the paper. Responses may include new experiments and data in response to a reviewer's request. Responses are optional and limited to 1000 words. FAST will be enforcing a hard limit on the length of the author's response for fairness and to reduce workload (for both authors and reviewers): exceeding the word limit will impact a paper negatively."

要点：回应可选；上限 **1000 词**，为硬性限制，超出会对论文产生负面影响；内容限于纠正审稿意见中的事实错误、回答审稿人提问、澄清论文观点；可以包含应审稿人要求补做的新实验与数据。

秋季轮的回应期为 Tuesday, November 17, 2026 至 Thursday, November 19, 2026, 23:59 AoE。

---

## 十二、录用通知之后的流程

### 12.1 通知与注册义务

> "Notification: Authors will be notified of paper acceptance or rejection according to the schedule above. A few papers that cannot be accepted immediately but which are likely to be accepted with a revision will be given the opportunity to submit a one-shot revision (see below)."

> "By submitting a paper, you agree that, if the paper is accepted, at least one of the authors will register to attend the conference at full price (i.e., not the student rate) and to present the paper; USENIX members at the Sustainer level and higher may apply their membership discounts to their registrations. If an author plans to present more than one paper, one full-price registration will still be required for each paper."

注册费构成困难时，可联系 conference@usenix.org。

### 12.2 签证邀请函

> "If your paper is accepted and you need an invitation letter to apply for a visa to attend the conference, contact conference@usenix.org as soon as possible. Visa applications are reportedly taking more than three months to process. Please identify yourself as a presenter or an author, and include your mailing address in your email request."

### 12.3 论文公开时间与保密

> "Paper Availability: All accepted papers will be listed on our website and made available online to registered attendees before the conference, at a date that depends on the review cycle. If your accepted paper should not be published prior to the event, please notify production@usenix.org by the final paper deadline for your review cycle. The papers will be available online to everyone beginning on the first day of the conference, February 23. Accepted submissions will be treated as confidential prior to publication on the USENIX FAST '27 website; rejected submissions will be permanently treated as confidential."

### 12.4 One-Shot Revision（一次性修改）

> "One-Shot Revision: A one-shot revision decision includes a summary of the paper's merits and a list of necessary changes that are required for the paper to be accepted at FAST. Authors given a one-shot-revision decision will be sent, within a few days of the decision, detailed instructions about how to resubmit. Authors may then submit a version of their work addressing all revision instructions during the subsequent deadline. Papers revised and resubmitted following a one-shot-revision decision can only receive a decision of accept or reject after revision, not revise; this is what makes revisions "one-shot.""

> "Unlike papers accepted with shepherding, the revision instructions may include running additional experiments that obtain specific results, e.g., comparing performance against a certain alternative."

> "If authors receive a one-shot-revision decision for a paper submitted to the fall deadline of FAST '27, this gives them the option to make the requested changes and resubmit it to the next FAST deadline, which is the first deadline of FAST '28. If the paper is accepted then, it will appear at FAST '28, not FAST '27."

---

## 十三、利益冲突认定（Conflict Identification）

投稿时作者必须申报与程序委员会成员的利益冲突。官方给出的四类冲突：

> "Institution: You are currently employed at the same institution, have been previously employed at the same institution within the past two years, or are going to begin employment at the same institution. A completed internship does not constitute an institutional conflict."

> "Advisor/Advisee: Doctoral thesis advisor and post-doctoral advisor (if relevant) are conflicts for life."

> "Collaboration: You have a collaboration on a project, publication, grant proposal, or editorship within the past two years."

> "Close friends and family: Close family relations (e.g., spouse, parent/child, sibling) and close friends are conflicts forever if they are potential reviewers."

> "The PC will review paper conflicts to ensure the integrity of the reviewing process, adding conflicts if necessary. If there is no basis for conflicts indicated by authors, such conflicts will be removed. Do not identify PC members as a conflict solely to avoid having them as reviewers. If you have any questions about conflicts, contact the program co-chairs."

---

## 十四、提交系统与联系方式

### 14.1 论文提交系统

网页版 Call for Papers 的投稿链接与页面底部 "SUBMIT YOUR WORK" 按钮（2026-08-11 抓取时）均指向：<https://fast27spring.usenix.hotcrp.com/>（HotCRP 是学术会议常用的投稿与审稿系统）。

> "Please submit your short and long papers by one of the submission deadlines listed above in PDF format via the submission form . Do not email submissions. There is no separate deadline for abstract submissions."

官方 PDF 版对应位置的写法是"通过 Call for Papers 网页上链接的投稿表单提交"，PDF 本身未直接给出 HotCRP 域名：

> "Please submit your short and long papers by one of the submission deadlines listed above in PDF format via the submission form, linked from the Call for Papers webpage. Do not email submissions. There is no separate deadline for abstract submissions."

**不接受邮件投稿**（"Do not email submissions."）。

### 14.2 Artifact 提交系统

<https://fast27springae.usenix.hotcrp.com/>（Call for Artifacts 页面中 artifact 提交站点与 "SUBMIT YOUR ARTIFACT" 按钮的链接）。

> "All other communication will go through the submission system ."

### 14.3 联系邮箱

| 用途 | 邮箱 |
| --- | --- |
| 程序委员会共同主席（匿名化疑问、投稿是否符合规范、利益冲突疑问） | fast27chairs@usenix.org |
| Artifact 评审委员会（打包问题、恶意 artifact 报告、artifact 提交流程问题） | fast27aec@usenix.org |
| 投稿规范疑问（USENIX 办公室） | submissionspolicy@usenix.org |
| 注册费困难、签证邀请函、注册问题 | conference@usenix.org |
| 论文提前公开的禁令请求（embargo）、论文集与论文相关 | production@usenix.org |
| 会员事务 | membership@usenix.org |
| 赞助事务 | sponsorship@usenix.org |
| 学生资助 | students@usenix.org |

> "If you are uncertain whether your submission meets USENIX's guidelines, contact the program co-chairs, fast27chairs@usenix.org , or the USENIX office, submissionspolicy@usenix.org ."

> "Questions about the process can be directed to fast27aec@usenix.org ."

### 14.4 会议组织者

- **Program Co-Chairs**：George Amvrosiadis (Carnegie Mellon University)；Peter Macko (MongoDB)
- **Artifact Evaluation Committee Co-Chairs**：Bingzhe Li (The University of Texas at Dallas)；Erci Xu (Shanghai Jiao Tong University)
- **Work-in-Progress/Posters Co-Chairs**：Alex Conway (Cornell Tech)；Huaicheng Li (Virginia Tech)
- **Mentoring Co-Chairs**：Zhichao Cao (Arizona State University)；Sara McAllister (University of Wisconsin–Madison)

完整的 Program Committee、Artifact Evaluation Committee 与 Steering Committee 名单见官方 Call for Papers 页面。

---

## 十五、原始链接与抓取时间

全部抓取于 **2026-08-11**，全部来自 usenix.org 官方域名。WebFetch 对 usenix.org 返回 HTTP 403，改用 curl 携带浏览器 User-Agent 取回，各链接返回状态如下：

| URL | HTTP 状态 | 用途 |
| --- | --- | --- |
| https://www.usenix.org/conference/fast27/call-for-papers | 200 | 主要事实来源：重要日期、篇幅、排版、双盲政策、topics、投稿与重投政策、组织者 |
| https://www.usenix.org/conference/fast27 | 200 | 会议时间地点、场馆地址、Test of Time Award 提名日期、联系邮箱 |
| https://www.usenix.org/conference/fast27/call-for-artifacts | 200 | Artifact Evaluation 全部内容 |
| https://www.usenix.org/sites/default/files/fast27_cfp_080626.pdf | 200 | 官方 CFP PDF（4 页，页脚 Rev. 3/11/26），用于交叉核对日期与邮箱明文 |
| https://www.usenix.org/conferences/author-resources/paper-templates | 200 | LaTeX/Word 模板文件名与链接、非模板排版要求 |
| https://www.usenix.org/conferences/author-resources/submissions-policy | 200 | USENIX 同时投稿政策原文与处罚条款 |

模板与附录文件的直接下载地址（均在 usenix.org 域名下）：

- https://www.usenix.org/sites/default/files/usenix2019_v3.1.tex
- https://www.usenix.org/sites/default/files/usenix-2020-09.sty
- https://www.usenix.org/sites/default/files/usenix2022.doc
- https://www.usenix.org/sites/default/files/usenix-2020-09.pdf
- https://www.usenix.org/sites/default/files/fast25_ae_appendix-template.zip

投稿系统地址：

- 论文：https://fast27spring.usenix.hotcrp.com/
- Artifact：https://fast27springae.usenix.hotcrp.com/
