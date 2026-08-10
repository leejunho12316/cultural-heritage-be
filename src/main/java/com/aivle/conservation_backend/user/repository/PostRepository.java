package com.aivle.conservation_backend.user.repository;

import com.aivle.conservation_backend.user.domain.Post;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.EntityGraph;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Optional;

public interface PostRepository extends JpaRepository<Post, Long> {

    @Override
    @EntityGraph(attributePaths = "author")
    Page<Post> findAll(Pageable pageable);

    @EntityGraph(attributePaths = "author")
    Page<Post> findByTitleContainingIgnoreCase(
            String title,
            Pageable pageable
    );

    @EntityGraph(attributePaths = "author")
    Page<Post> findByAuthor_NicknameContainingIgnoreCase(
            String nickname,
            Pageable pageable
    );

    @EntityGraph(attributePaths = "author")
    Page<Post> findByAuthor_Id(
            Long authorId,
            Pageable pageable
    );

    @EntityGraph(attributePaths = "author")
    Optional<Post> findPostById(Long id);

    @Modifying(
            clearAutomatically = true,
            flushAutomatically = true
    )
    @Query("""
        delete from Post p
        where p.author.id = :authorId
        """)
    int deleteAllByAuthorId(
            @Param("authorId") Long authorId
    );
}